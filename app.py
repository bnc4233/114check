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
    Parses Naver ad parameters from the URL and checks the referrer.
    Naver parameters: n_media, n_query, n_rank, n_ad_group, n_ad, n_keyword
    """
    is_naver_ad = False
    keyword = None
    media = None
    ad_group = None
    ad_id = None

    if not url:
        return is_naver_ad, keyword, media, ad_group, ad_id

    # Parse URL parameters
    parsed_url = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed_url.query)

    # Check for Naver tracking parameters (e.g. n_media, n_query)
    # The parameters are returned as lists by parse_qs
    if 'n_media' in params or 'n_query' in params or 'n_keyword' in params:
        is_naver_ad = True
        
        # Decode keyword (usually in n_query)
        if 'n_query' in params:
            raw_keyword = params['n_query'][0]
            try:
                # Naver keyword is URL encoded
                keyword = urllib.parse.unquote(raw_keyword)
            except Exception:
                keyword = raw_keyword
        
        # Extract media source
        if 'n_media' in params:
            media_code = params['n_media'][0]
            # Map common Naver media codes if known, else display code
            media_map = {
                '1': '네이버_통합검색(PC)',
                '2': '네이버_통합검색(모바일)',
                '3': '네이버_지식iN',
                '4': '네이버_블로그/카페',
                '5': '네이버_쇼핑'
            }
            media = media_map.get(media_code, f"기타매체({media_code})")
        
        # Extract ad group and ad ID
        if 'n_ad_group' in params:
            ad_group = params['n_ad_group'][0]
        if 'n_ad' in params:
            ad_id = params['n_ad'][0]

    # Double check Referrer as fallback for organic Naver search or ad clicks
    if not is_naver_ad and referrer:
        parsed_ref = urllib.parse.urlparse(referrer)
        if 'ad.search.naver.com' in parsed_ref.netloc:
            is_naver_ad = True
            # Try to grab keywords if any search query is in the referrer
            ref_params = urllib.parse.parse_qs(parsed_ref.query)
            if 'q' in ref_params:
                try:
                    keyword = urllib.parse.unquote(ref_params['q'][0])
                except Exception:
                    pass
        elif 'search.naver.com' in parsed_ref.netloc:
            # Note: This is regular Naver search (Organic), not search ads.
            # We keep is_naver_ad = False but could log keyword if needed.
            ref_params = urllib.parse.parse_qs(parsed_ref.query)
            if 'query' in ref_params:
                try:
                    keyword = urllib.parse.unquote(ref_params['query'][0])
                except Exception:
                    pass

    return is_naver_ad, keyword, media, ad_group, ad_id


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
    recent_logs = get_recent_logs(limit=50)
    return jsonify({
        "stats": stats,
        "logs": recent_logs
    })


if __name__ == '__main__':
    # Flask port mapping for local development
    port = int(os.environ.get('PORT', 5000))
    # In production, Render handles SSL/TLS and we bind to 0.0.0.0
    app.run(host='0.0.0.0', port=port, debug=True)
