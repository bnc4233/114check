import os
import urllib.parse
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
from database import init_db, log_visitor, get_recent_logs, get_stats_summary, reclassify_past_logs, keep_alive_db

app = Flask(__name__)
# Enable CORS for tracking requests coming from other domains (e.g. your main website)
CORS(app)

# Initialize database on startup
init_db()

def get_client_ip():
    """Extracts the real client IP, dealing with Render's proxy setup."""
    x_forwarded_for = request.headers.get('X-Forwarded-For')
    if x_forwarded_for:
        # X-Forwarded-For can contain a list of IPs: "client, proxy1, proxy2"
        # The first IP is the real client.
        return x_forwarded_for.split(',')[0].strip()
    return request.remote_addr or 'Unknown'

def detect_device(user_agent):
    """Simple parser to detect device type based on user-agent."""
    if not user_agent:
        return 'Desktop'
    ua = user_agent.lower()
    if 'mobi' in ua or 'android' in ua or 'iphone' in ua:
        if 'ipad' in ua or 'tablet' in ua:
            return 'Tablet'
        return 'Mobile'
    return 'Desktop'

def parse_naver_ads(url, referrer):
    """
    Parses Naver and Daum ad parameters from the URL and checks the referrer.
    Supports standard ads, keyword match, and expanded search (확장 검색).
    - Return value for is_ad_type: 0 (Organic), 1 (Naver Ad), 2 (Daum Ad)
    """
    is_ad_type = 0
    keyword = None
    media = None
    ad_group = None
    ad_id = None

    if not url:
        return is_ad_type, keyword, media, ad_group, ad_id

    # Parse URL parameters
    parsed_url = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed_url.query)

    # 1. Comprehensive check for Naver tracking parameters (standard + expanded search)
    naver_keys = {
        'n_media', 'n_query', 'n_keyword', 'n_ad_group', 'n_ad',
        'n_match', 'n_rank', 'n_campaign_type', 'n_ad_group_type',
        'n_network', 'n_keyword_id', 'n_contract', 'n_mall_pid'
    }
    has_naver_param = any(k in params for k in naver_keys) or any(k.startswith('n_') for k in params)

    if has_naver_param:
        is_ad_type = 1
        
        # 1-1. Decode keyword: prefer user actual search query (n_query), then registered keyword (n_keyword)
        if 'n_query' in params and params['n_query'][0].strip():
            raw_keyword = params['n_query'][0].strip()
            try:
                keyword = urllib.parse.unquote(raw_keyword)
            except Exception:
                keyword = raw_keyword
        elif 'n_keyword' in params and params['n_keyword'][0].strip():
            raw_keyword = params['n_keyword'][0].strip()
            try:
                keyword = urllib.parse.unquote(raw_keyword)
            except Exception:
                keyword = raw_keyword

        # If keyword is still empty (common in Naver expanded search '확장 검색'), check referrer or fallback
        if not keyword:
            if referrer:
                try:
                    parsed_ref = urllib.parse.urlparse(referrer)
                    ref_params = urllib.parse.parse_qs(parsed_ref.query)
                    if 'query' in ref_params and ref_params['query'][0].strip():
                        keyword = urllib.parse.unquote(ref_params['query'][0].strip())
                    elif 'q' in ref_params and ref_params['q'][0].strip():
                        keyword = urllib.parse.unquote(ref_params['q'][0].strip())
                except Exception:
                    pass

            # If still not found, label it clearly according to Naver Ad Manager standard
            if not keyword:
                match_code = params.get('n_match', [''])[0]
                if match_code == '2':
                    keyword = '확장 검색 결과'
                elif 'n_ad_group' in params or 'n_ad' in params:
                    keyword = '확장 검색 결과'
                else:
                    keyword = '네이버 광고 유입'
        
        # 1-2. Extract match type suffix (e.g. n_match=2 is 키워드 확장 / 확장 검색)
        match_code = params.get('n_match', [''])[0]
        match_suffix = ''
        if match_code == '2':
            match_suffix = '(확장검색)'
        elif match_code == '3':
            match_suffix = '(연관검색)'
        elif match_code == '4':
            match_suffix = '(유사검색)'
        elif match_code == '5':
            match_suffix = '(스마트블록)'

        # 1-3. Extract media source
        if 'n_media' in params:
            media_code = params['n_media'][0]
            media_map = {
                '1': '네이버_통합검색(PC)',
                '2': '네이버_통합검색(모바일)',
                '3': '네이버_지식iN',
                '4': '네이버_블로그/카페',
                '5': '네이버_쇼핑'
            }
            base_media = media_map.get(media_code, f"네이버_기타매체({media_code})")
            media = f"{base_media} {match_suffix}".strip() if match_suffix else base_media
        else:
            media = f"네이버_파워링크 {match_suffix}".strip() if match_suffix else '네이버_파워링크(확장)'
        
        # 1-4. Extract ad group and ad ID
        if 'n_ad_group' in params:
            ad_group = params['n_ad_group'][0]
        if 'n_ad' in params:
            ad_id = params['n_ad'][0]

    # 2. Check for Daum / Kakao Keyword Ads tracking parameters (e.g. DMKW, k_query, k_keyword)
    elif any(k in params for k in ['DMKW', 'DMCOL', 'k_query', 'k_keyword', 'k_campaign', 'k_adgroup', 'k_ad']):
        is_ad_type = 2
        
        # Extract keyword (k_query is actual user query, DMKW is target keyword, k_keyword is registered keyword)
        if 'k_query' in params:
            raw_keyword = params['k_query'][0]
        elif 'DMKW' in params:
            raw_keyword = params['DMKW'][0]
        elif 'k_keyword' in params:
            raw_keyword = params['k_keyword'][0]
        else:
            raw_keyword = None

        if raw_keyword:
            try:
                keyword = urllib.parse.unquote(raw_keyword)
            except Exception:
                keyword = raw_keyword

        # Extract media / collection
        if 'DMCOL' in params:
            col_code = params['DMCOL'][0].upper()
            col_map = {
                'PM': '다음_프리미엄링크',
                'SM': '다음_와이드링크',
                'MOBILE': '다음_모바일웹'
            }
            media = col_map.get(col_code, f"다음_기타매체({col_code})")
        else:
            media = '다음_검색광고'

        # Extract campaign and adgroup as group and ad IDs
        if 'k_campaign' in params:
            ad_group = f"캠페인:{params['k_campaign'][0]}"
        if 'k_adgroup' in params:
            ad_id = f"그룹:{params['k_adgroup'][0]}"

    # Double check Referrer as fallback
    if is_ad_type == 0 and referrer:
        try:
            parsed_ref = urllib.parse.urlparse(referrer)
            if 'ad.search.naver.com' in parsed_ref.netloc:
                is_ad_type = 1
                media = '네이버_광고검색(리퍼러)'
                ref_params = urllib.parse.parse_qs(parsed_ref.query)
                if 'q' in ref_params and ref_params['q'][0].strip():
                    try:
                        keyword = urllib.parse.unquote(ref_params['q'][0].strip())
                    except Exception:
                        pass
                if not keyword:
                    keyword = '확장 검색 결과'
            elif 'search.daum.net' in parsed_ref.netloc:
                # Daum Organic search (not ad)
                ref_params = urllib.parse.parse_qs(parsed_ref.query)
                if 'q' in ref_params:
                    try:
                        keyword = urllib.parse.unquote(ref_params['q'][0])
                    except Exception:
                        pass
            elif 'search.naver.com' in parsed_ref.netloc:
                ref_params = urllib.parse.parse_qs(parsed_ref.query)
                if 'query' in ref_params:
                    try:
                        keyword = urllib.parse.unquote(ref_params['query'][0])
                    except Exception:
                        pass
        except Exception:
            pass

    return is_ad_type, keyword, media, ad_group, ad_id


# Safely reclassify existing past logs to include expanded search ads (does NOT delete any data)
try:
    reclassify_past_logs(parse_naver_ads)
except Exception as e:
    print(f"Warning: Failed to run initial reclassification: {e}")


@app.route('/ping', methods=['GET'])
def ping():
    """
    UptimeRobot & Keep-Alive Monitoring Endpoint.
    Does NOT log any visitor to keep clean statistics,
    but runs a lightweight read query to prevent Supabase free-tier auto-pause.
    """
    db_status = keep_alive_db()
    return f"서버 살아있음! | {db_status}", 200


SITE_DOMAIN_MAPPING = {
    'lan.bizsmart.kr': 'smart-net',
    'smart-net': 'smart-net',
    'smartnet': 'smart-net',
    'm2m.bizsmart.kr': 'm2m',
    'm2m': 'm2m',
    'number.bizsmart.kr': 'number',
    'number': 'number',
    'uplus1': 'number',
    '114check': '기업통신114 (114check.com)',
    'xn--114-vg9lq14f91dhn0a.com': '기업통신114 (114check.com)',
}

def extract_site_id(provided_site, url):
    """Determines site_id from explicit site parameter or falls back to extracted URL domain."""
    if provided_site and str(provided_site).strip():
        clean_site = str(provided_site).strip()
        if clean_site in SITE_DOMAIN_MAPPING:
            return SITE_DOMAIN_MAPPING[clean_site]
        return clean_site
    if url:
        try:
            parsed = urllib.parse.urlparse(url)
            netloc = parsed.netloc or parsed.path.split('/')[0]
            if netloc:
                clean_host = netloc.split(':')[0].replace('www.', '')
                if clean_host in SITE_DOMAIN_MAPPING:
                    return SITE_DOMAIN_MAPPING[clean_host]
                if clean_host:
                    return clean_host
        except Exception:
            pass
    return 'default'


@app.route('/track', methods=['POST'])
def track():
    """
    Visitor Tracking Endpoint.
    Receives JSON payload with url, referrer, userAgent, and site_id.
    """
    data = request.get_json(silent=True) or {}
    
    url = data.get('url')
    referrer = data.get('referrer')
    user_agent = data.get('userAgent') or request.headers.get('User-Agent', '')
    raw_site_id = data.get('site_id') or data.get('site')
    
    # 1. Determine site_id
    site_id = extract_site_id(raw_site_id, url)

    # 1-1. Ignore tracking for deleted/discontinued sites
    if site_id in ('mobile-phone', 'mobilephone.bizsmart.kr', 'mobilephone'):
        return jsonify({"status": "ignored", "recorded": False}), 200

    # 2. Get real visitor IP
    ip_address = get_client_ip()
    
    # 3. Parse Naver Ads indicators
    is_naver_ad, keyword, media, ad_group, ad_id = parse_naver_ads(url, referrer)
    
    # 4. Detect device type
    device_type = detect_device(user_agent)
    
    # 5. Log to database
    log_visitor(
        ip_address=ip_address,
        referrer=referrer,
        user_agent=user_agent,
        full_url=url,
        is_naver_ad=is_naver_ad,
        naver_keyword=keyword,
        naver_media=media,
        naver_ad_group=ad_group,
        naver_ad=ad_id,
        device_type=device_type,
        site_id=site_id
    )
    
    return jsonify({"status": "success", "recorded": True, "site_id": site_id}), 200


@app.route('/tracker.js', methods=['GET'])
def tracker_js():
    """
    Generates a dynamic tracking JavaScript file.
    Automatically detects the server host so that it points to the correct /track URL.
    Supports ?site=uplus1 parameter or data-site attribute.
    """
    # Build host URL
    protocol = "https" if request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https' else "http"
    host_url = f"{protocol}://{request.host}"
    
    query_site = request.args.get('site') or request.args.get('site_id') or request.args.get('s') or ''
    
    js_content = f"""
    (function() {{
        // Real-time Traffic Tracker Script
        try {{
            var currentScript = document.currentScript;
            var scriptSite = "{query_site}";
            if (!scriptSite && currentScript && currentScript.src) {{
                try {{
                    var scriptUrl = new URL(currentScript.src);
                    scriptSite = scriptUrl.searchParams.get('site') || scriptUrl.searchParams.get('site_id') || scriptUrl.searchParams.get('s') || '';
                }} catch(e) {{}}
            }}
            if (!scriptSite && currentScript) {{
                scriptSite = currentScript.getAttribute('data-site') || '';
            }}
            
            var trackingData = {{
                site_id: scriptSite,
                url: window.location.href,
                referrer: document.referrer,
                userAgent: navigator.userAgent,
                screenResolution: window.screen.width + 'x' + window.screen.height
            }};
            
            // Send tracking data to the backend server
            var xhr = new XMLHttpRequest();
            xhr.open('POST', '{host_url}/track', true);
            xhr.setRequestHeader('Content-Type', 'application/json;charset=UTF-8');
            xhr.send(JSON.stringify(trackingData));
        }} catch(e) {{
            console.error('Tracker error:', e);
        }}
    }})();
    """
    return Response(js_content, mimetype='application/javascript')


@app.route('/admin', methods=['GET'])
def admin():
    """Serves the main administrator monitoring dashboard page."""
    return render_template('admin.html')


@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Returns real-time dashboard data in JSON format."""
    stats = get_stats_summary()
    recent_logs = get_recent_logs(limit=200)
    return jsonify({
        "stats": stats,
        "logs": recent_logs
    })


if __name__ == '__main__':
    # Flask port mapping for local development
    port = int(os.environ.get('PORT', 5000))
    # In production, Render handles SSL/TLS and we bind to 0.0.0.0
    app.run(host='0.0.0.0', port=port, debug=True)
