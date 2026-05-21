import os
import urllib.parse
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
from database import init_db, log_visitor, get_recent_logs, get_stats_summary

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

    # 1. Check for Naver tracking parameters (e.g. n_media, n_query)
    if 'n_media' in params or 'n_query' in params or 'n_keyword' in params:
        is_ad_type = 1
        
        # Decode keyword (usually in n_query)
        if 'n_query' in params:
            raw_keyword = params['n_query'][0]
            try:
                keyword = urllib.parse.unquote(raw_keyword)
            except Exception:
                keyword = raw_keyword
        
        # Extract media source
        if 'n_media' in params:
            media_code = params['n_media'][0]
            media_map = {
                '1': '네이버_통합검색(PC)',
                '2': '네이버_통합검색(모바일)',
                '3': '네이버_지식iN',
                '4': '네이버_블로그/카페',
                '5': '네이버_쇼핑'
            }
            media = media_map.get(media_code, f"네이버_기타매체({media_code})")
        
        # Extract ad group and ad ID
        if 'n_ad_group' in params:
            ad_group = params['n_ad_group'][0]
        if 'n_ad' in params:
            ad_id = params['n_ad'][0]

    # 2. Check for Daum / Kakao Keyword Ads tracking parameters (e.g. DMKW, k_query, k_keyword)
    elif any(k in params for k in ['DMKW', 'DMCOL', 'k_query', 'k_keyword', 'k_campaign']):
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
        parsed_ref = urllib.parse.urlparse(referrer)
        if 'ad.search.naver.com' in parsed_ref.netloc:
            is_ad_type = 1
            ref_params = urllib.parse.parse_qs(parsed_ref.query)
            if 'q' in ref_params:
                try:
                    keyword = urllib.parse.unquote(ref_params['q'][0])
                except Exception:
                    pass
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

    return is_ad_type, keyword, media, ad_group, ad_id


@app.route('/ping', methods=['GET'])
def ping():
    """
    UptimeRobot Monitoring Endpoint.
    Does NOT log anything to the database to keep clean statistics.
    Simply returns text "서버 살아있음!"
    """
    return "서버 살아있음!", 200


@app.route('/track', methods=['POST'])
def track():
    """
    Visitor Tracking Endpoint.
    Receives JSON payload with url, referrer, and userAgent.
    """
    data = request.get_json(silent=True) or {}
    
    url = data.get('url')
    referrer = data.get('referrer')
    user_agent = data.get('userAgent') or request.headers.get('User-Agent', '')
    
    # 1. Get real visitor IP
    ip_address = get_client_ip()
    
    # 2. Parse Naver Ads indicators
    is_naver_ad, keyword, media, ad_group, ad_id = parse_naver_ads(url, referrer)
    
    # 3. Detect device type
    device_type = detect_device(user_agent)
    
    # 4. Log to database
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
        device_type=device_type
    )
    
    return jsonify({"status": "success", "recorded": True}), 200


@app.route('/tracker.js', methods=['GET'])
def tracker_js():
    """
    Generates a dynamic tracking JavaScript file.
    Automatically detects the server host so that it points to the correct /track URL.
    """
    # Build host URL
    protocol = "https" if request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https' else "http"
    host_url = f"{protocol}://{request.host}"
    
    js_content = f"""
    (function() {{
        // Real-time Traffic Tracker Script
        try {{
            var trackingData = {{
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
    recent_logs = get_recent_logs(limit=100)
    return jsonify({
        "stats": stats,
        "logs": recent_logs
    })


if __name__ == '__main__':
    # Flask port mapping for local development
    port = int(os.environ.get('PORT', 5000))
    # In production, Render handles SSL/TLS and we bind to 0.0.0.0
    app.run(host='0.0.0.0', port=port, debug=True)
