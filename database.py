import sqlite3
import os
from datetime import datetime, timedelta

# Import psycopg2 for PostgreSQL support
try:
    import psycopg2
    import psycopg2.extras
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')
DATABASE_URL = os.environ.get('DATABASE_URL')

def is_postgres():
    """Checks whether to use PostgreSQL based on environment variables."""
    return HAS_POSTGRES and DATABASE_URL and (DATABASE_URL.startswith('postgres://') or DATABASE_URL.startswith('postgresql://'))

def get_db_connection():
    """Returns a connection depending on the environment (PostgreSQL on Render, SQLite locally)."""
    if is_postgres():
        url = DATABASE_URL
        # Render's DATABASE_URL can start with postgres://, but psycopg2 prefers postgresql://
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        conn = psycopg2.connect(url, sslmode='require')
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    """Initializes the database and creates the logs table if it doesn't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if is_postgres():
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS visitor_logs (
                id SERIAL PRIMARY KEY,
                timestamp VARCHAR(30) NOT NULL,
                ip_address VARCHAR(50) NOT NULL,
                referrer TEXT,
                user_agent TEXT,
                full_url TEXT,
                is_naver_ad INTEGER DEFAULT 0,
                naver_keyword VARCHAR(255),
                naver_media VARCHAR(255),
                naver_ad_group VARCHAR(255),
                naver_ad VARCHAR(255),
                device_type VARCHAR(50)
            )
        ''')
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS visitor_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ip_address TEXT NOT NULL,
                referrer TEXT,
                user_agent TEXT,
                full_url TEXT,
                is_naver_ad INTEGER DEFAULT 0,
                naver_keyword TEXT,
                naver_media TEXT,
                naver_ad_group TEXT,
                naver_ad TEXT,
                device_type TEXT
            )
        ''')
    conn.commit()
    cursor.close()
    conn.close()

def log_visitor(ip_address, referrer, user_agent, full_url, is_naver_ad, naver_keyword=None, naver_media=None, naver_ad_group=None, naver_ad=None, device_type='Unknown'):
    """Logs a new visitor entry. Works dynamically on both SQLite and PostgreSQL."""
    kst_now = datetime.utcnow() + timedelta(hours=9)
    timestamp_str = kst_now.strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Handle different placeholders (? for SQLite, %s for PostgreSQL)
    placeholder = '%s' if is_postgres() else '?'
    query = f'''
        INSERT INTO visitor_logs (
            timestamp, ip_address, referrer, user_agent, full_url, 
            is_naver_ad, naver_keyword, naver_media, naver_ad_group, naver_ad, device_type
        ) VALUES ({", ".join([placeholder]*11)})
    '''
    
    params = (
        timestamp_str, ip_address, referrer, user_agent, full_url,
        1 if is_naver_ad else 0, naver_keyword, naver_media, naver_ad_group, naver_ad, device_type
    )
    
    cursor.execute(query, params)
    conn.commit()
    cursor.close()
    conn.close()

def get_recent_logs(limit=100):
    """Retrieves recent logs including the cumulative IP visit count across both engines."""
    conn = get_db_connection()
    if is_postgres():
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('''
            SELECT v.*, 
                   (SELECT COUNT(*)::integer FROM visitor_logs WHERE ip_address = v.ip_address) as ip_visit_count
            FROM visitor_logs v
            ORDER BY v.id DESC
            LIMIT %s
        ''', (limit,))
        logs = [dict(row) for row in cursor.fetchall()]
    else:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT v.*, 
                   (SELECT COUNT(*) FROM visitor_logs WHERE ip_address = v.ip_address) as ip_visit_count
            FROM visitor_logs v
            ORDER BY v.id DESC
            LIMIT ?
        ''', (limit,))
        logs = [dict(row) for row in cursor.fetchall()]
        
    cursor.close()
    conn.close()
    return logs

def get_stats_summary():
    """Calculates statistics dynamically with SQL dialect adaptations."""
    conn = get_db_connection()
    
    if is_postgres():
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cursor = conn.cursor()
        
    # Total visitors
    cursor.execute('SELECT COUNT(*) FROM visitor_logs')
    row = cursor.fetchone()
    if is_postgres():
        total_visitors = list(row.values())[0] if row else 0
    else:
        total_visitors = row[0] if row else 0
        
    # Naver Ad visitors
    cursor.execute('SELECT COUNT(*) FROM visitor_logs WHERE is_naver_ad = 1')
    row = cursor.fetchone()
    if is_postgres():
        naver_ad_visitors = list(row.values())[0] if row else 0
    else:
        naver_ad_visitors = row[0] if row else 0
        
    organic_visitors = total_visitors - naver_ad_visitors
    
    # Top keywords from Naver ads
    cursor.execute('''
        SELECT naver_keyword, COUNT(*) as count 
        FROM visitor_logs 
        WHERE is_naver_ad = 1 AND naver_keyword IS NOT NULL AND naver_keyword != ''
        GROUP BY naver_keyword 
        ORDER BY count DESC 
        LIMIT 5
    ''')
    top_keywords = [dict(row) for row in cursor.fetchall()]
    
    # Top referrers
    cursor.execute('''
        SELECT referrer, COUNT(*) as count 
        FROM visitor_logs 
        WHERE referrer IS NOT NULL AND referrer != ''
        GROUP BY referrer 
        ORDER BY count DESC 
        LIMIT 5
    ''')
    top_referrers = [dict(row) for row in cursor.fetchall()]

    # Traffic source by hour (last 24 hours)
    kst_now = datetime.utcnow() + timedelta(hours=9)
    kst_24h_ago = (kst_now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    
    if is_postgres():
        cursor.execute('''
            SELECT SUBSTRING(timestamp FROM 12 FOR 2) as hour, 
                   SUM(CASE WHEN is_naver_ad = 1 THEN 1 ELSE 0 END)::integer as naver_count,
                   SUM(CASE WHEN is_naver_ad = 0 THEN 1 ELSE 0 END)::integer as other_count
            FROM visitor_logs
            WHERE timestamp >= %s
            GROUP BY hour
            ORDER BY hour ASC
        ''', (kst_24h_ago,))
    else:
        cursor.execute('''
            SELECT strftime('%H', timestamp) as hour, 
                   SUM(CASE WHEN is_naver_ad = 1 THEN 1 ELSE 0 END) as naver_count,
                   SUM(CASE WHEN is_naver_ad = 0 THEN 1 ELSE 0 END) as other_count
            FROM visitor_logs
            WHERE timestamp >= ?
            GROUP BY hour
            ORDER BY hour ASC
        ''', (kst_24h_ago,))
        
    hourly_traffic = [dict(row) for row in cursor.fetchall()]

    cursor.close()
    conn.close()
    
    return {
        'total_visitors': total_visitors,
        'naver_ad_visitors': naver_ad_visitors,
        'organic_visitors': organic_visitors,
        'naver_ratio': round((naver_ad_visitors / total_visitors * 100), 1) if total_visitors > 0 else 0,
        'top_keywords': top_keywords,
        'top_referrers': top_referrers,
        'hourly_traffic': hourly_traffic
    }
