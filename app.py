__author__ = "TL"
__copyright__ = ""
__version__ = "1.0"
__app_name__ = "Scraper"

#API
APIFY_API_TOKEN  = ""
ANTHROPIC_API_KEY = ""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading, os, sys, csv, json, re, subprocess, datetime, sqlite3, hashlib, shutil
from collections import Counter, defaultdict
from pathlib import Path

# Config
DB_FILE = "scraper.db"              # SQLite
BASE_OUTPUT_DIR = "Scraper"         # outp folder
ALL_FOLDER = "_All"                 # subfolder with all Doc
CHUNK_SIZE = 1500                   # Chars per trans


#Data Base

def init_db():
    """Create all tables if they don't exist."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Main video table
    c.execute("""CREATE TABLE IF NOT EXISTS videos (
        video_id TEXT PRIMARY KEY,
        title TEXT, url TEXT, channel TEXT, published TEXT,
        duration TEXT, views TEXT, category TEXT, difficulty TEXT,
        quality_score REAL, core_idea TEXT, actionable_insight TEXT,
        target_audience TEXT, creator_info TEXT,
        mentioned_companies TEXT, company_details TEXT,
        financial_data TEXT, technologies TEXT,
        auto_tags TEXT, semantic_topics TEXT,
        language TEXT, processed_at TEXT,
        project_category TEXT,
        transcript TEXT, qa_analysis TEXT, raw_json TEXT
    )""")

    # Ai Search -> Chunks
    c.execute("""CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY,
        video_id TEXT, chunk_index INTEGER,
        content TEXT, summary TEXT,
        FOREIGN KEY (video_id) REFERENCES videos(video_id)
    )""")

    # Facts statistics
    c.execute("""CREATE TABLE IF NOT EXISTS facts (
        fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id TEXT, fact_type TEXT,
        fact_text TEXT, confidence TEXT,
        FOREIGN KEY (video_id) REFERENCES videos(video_id)
    )""")

    # Entity Index
    c.execute("""CREATE TABLE IF NOT EXISTS entity_index (
        entity TEXT, entity_type TEXT, video_id TEXT,
        context TEXT,
        PRIMARY KEY (entity, video_id),
        FOREIGN KEY (video_id) REFERENCES videos(video_id)
    )""")

    # Channel stats
    c.execute("""CREATE TABLE IF NOT EXISTS channels (
        channel_name TEXT PRIMARY KEY,
        total_videos INTEGER DEFAULT 0,
        avg_quality REAL DEFAULT 0,
        top_category TEXT, top_technologies TEXT,
        first_seen TEXT, last_seen TEXT
    )""")

    # History - no duplicate
    c.execute("""CREATE TABLE IF NOT EXISTS history (
        video_id TEXT PRIMARY KEY,
        url TEXT,
        channel TEXT,
        title TEXT,
        status TEXT,
        first_attempt TEXT,
        last_attempt TEXT,
        attempt_count INTEGER DEFAULT 1
    )""")

    conn.commit()
    conn.close()


# history func

def history_check(video_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT status FROM history WHERE video_id=?", (video_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def history_mark(video_id, url="", channel="", title="", status="done"):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT attempt_count FROM history WHERE video_id=?", (video_id,))
    existing = c.fetchone()
    if existing:
        c.execute("""UPDATE history SET status=?, last_attempt=?, attempt_count=?, 
                     title=COALESCE(NULLIF(?, ''), title),
                     channel=COALESCE(NULLIF(?, ''), channel)
                     WHERE video_id=?""",
                  (status, now, existing[0] + 1, title, channel, video_id))
    else:
        c.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?,?)",
                  (video_id, url, channel, title, status, now, now, 1))
    conn.commit()
    conn.close()

def history_get_all():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT video_id, status FROM history")
    result = {row[0]: row[1] for row in c.fetchall()}
    conn.close()
    return result

def history_count():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM history")
    n = c.fetchone()[0]
    conn.close()
    return n


# DB FUNCTION INSERT

def db_insert_video(row, transcript="", qa_text="", raw_json=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO videos VALUES (
        :video_id, :title, :url, :channel, :published, :duration, :views,
        :category, :difficulty, :quality_score, :core_idea, :actionable_insight,
        :target_audience, :creator_info, :mentioned_companies, :company_details,
        :financial_data, :technologies, :auto_tags, :semantic_topics,
        :language, :processed_at, :project_category,
        :transcript, :qa_analysis, :raw_json
    )""", {**row, "transcript": transcript, "qa_analysis": qa_text, "raw_json": raw_json})
    conn.commit()
    conn.close()

#chunks
def db_insert_chunks(video_id, chunks):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM chunks WHERE video_id=?", (video_id,))
    for i, chunk in enumerate(chunks):
        cid = hashlib.md5(f"{video_id}_{i}".encode()).hexdigest()[:12]
        c.execute("INSERT INTO chunks VALUES (?,?,?,?,?)",
                  (cid, video_id, i, chunk["content"], chunk.get("summary", "")))
    conn.commit()
    conn.close()
#facts
def db_insert_facts(video_id, facts):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM facts WHERE video_id=?", (video_id,))
    for f in facts:
        c.execute("INSERT INTO facts (video_id, fact_type, fact_text, confidence) VALUES (?,?,?,?)",
                  (video_id, f.get("type", "claim"), f.get("text", ""), f.get("confidence", "medium")))
    conn.commit()
    conn.close()

def db_insert_entities(video_id, entities):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM entity_index WHERE video_id=?", (video_id,))
    for e in entities:
        c.execute("INSERT OR REPLACE INTO entity_index VALUES (?,?,?,?)",
                  (e.get("name", ""), e.get("type", ""), video_id, e.get("context", "")))
    conn.commit()
    conn.close()
#update stats
def db_update_channel_stats(channel_name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT category, quality_score, technologies, processed_at FROM videos WHERE channel=?", (channel_name,))
    rows = c.fetchall()
    if not rows: conn.close(); return
    cats = Counter(r[0] for r in rows if r[0])
    scores = [r[1] for r in rows if r[1]]
    techs = Counter()
    for r in rows:
        if r[2]:
            for t in r[2].split(","):
                t = t.strip()
                if t: techs[t] += 1
    dates = sorted(r[3] for r in rows if r[3])
    c.execute("INSERT OR REPLACE INTO channels VALUES (?,?,?,?,?,?,?)", (
        channel_name, len(rows),
        round(sum(scores) / len(scores), 1) if scores else 0,
        cats.most_common(1)[0][0] if cats else "",
        ", ".join(t for t, _ in techs.most_common(5)),
        dates[0] if dates else "", dates[-1] if dates else "",
    ))
    conn.commit()
    conn.close()

#search
def db_search(query="", category="", min_score=0, channel=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    sql = "SELECT video_id, title, url, channel, category, quality_score, difficulty, technologies, auto_tags, semantic_topics FROM videos WHERE 1=1"
    params = []
    if query:
        sql += " AND (title LIKE ? OR core_idea LIKE ? OR technologies LIKE ? OR auto_tags LIKE ? OR semantic_topics LIKE ?)"
        q = f"%{query}%"; params += [q, q, q, q, q]
    if category and category != "All": sql += " AND category=?"; params.append(category)
    if min_score > 0: sql += " AND quality_score>=?"; params.append(min_score)
    if channel and channel != "All": sql += " AND channel=?"; params.append(channel)
    sql += " ORDER BY processed_at DESC"
    c.execute(sql, params)
    r = c.fetchall()
    conn.close()
    return r

def db_get_all_categories():
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT DISTINCT category FROM videos WHERE category != '' ORDER BY category")
    r = [x[0] for x in c.fetchall()]; conn.close(); return r

def db_get_all_channels():
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT DISTINCT channel FROM videos WHERE channel != '' ORDER BY channel")
    r = [x[0] for x in c.fetchall()]; conn.close(); return r

def db_get_channel_comparison():
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT * FROM channels ORDER BY avg_quality DESC")
    r = c.fetchall(); conn.close(); return r

def db_get_video_count():
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM videos"); n = c.fetchone()[0]; conn.close(); return n

def db_get_stats():
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT COUNT(*), AVG(quality_score), COUNT(DISTINCT channel), COUNT(DISTINCT category) FROM videos")
    row = c.fetchone()
    c.execute("SELECT technologies FROM videos WHERE technologies != ''")
    tc = Counter()
    for r in c.fetchall():
        for t in r[0].split(","):
            t = t.strip()
            if t: tc[t] += 1
    conn.close()
    return {"total": row[0] or 0, "avg_score": round(row[1] or 0, 1),
            "channels": row[2] or 0, "categories": row[3] or 0, "top_tech": tc.most_common(10)}



def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name or "Unknown").strip("_")[:80]

def get_channel_dir(category, channel_name):
    #Scraper/Category/ChannelName/
    ch_safe = safe_name(channel_name)
    cat_safe = safe_name(category)
    path = os.path.join(BASE_OUTPUT_DIR, cat_safe, ch_safe)
    os.makedirs(path, exist_ok=True)
    return path

def get_all_dir():
    #Scraper/_All/
    path = os.path.join(BASE_OUTPUT_DIR, ALL_FOLDER)
    os.makedirs(path, exist_ok=True)
    return path

def get_channel_file(channel_dir, channel_name, suffix):
    #channelname prefix
    ch_safe = safe_name(channel_name)
    return os.path.join(channel_dir, f"{ch_safe}{suffix}")

def get_all_file(suffix):
    return os.path.join(get_all_dir(), f"{ALL_FOLDER}{suffix}")


#export - AI

def export_to_csv(filepath):
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    cols = [d[0] for d in c.execute("SELECT * FROM videos LIMIT 0").description
            if d[0] not in ("transcript", "qa_analysis", "raw_json")]
    c.execute(f"SELECT {','.join(cols)} FROM videos ORDER BY processed_at DESC")
    rows = c.fetchall(); conn.close()
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    return len(rows)

def export_knowledge_base_json(filepath):
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT * FROM videos ORDER BY quality_score DESC")
    cols = [d[0] for d in c.description]
    videos = []
    for row in c.fetchall():
        v = dict(zip(cols, row)); vid = v["video_id"]
        c.execute("SELECT chunk_index, content, summary FROM chunks WHERE video_id=? ORDER BY chunk_index", (vid,))
        v["chunks"] = [{"index": r[0], "content": r[1], "summary": r[2]} for r in c.fetchall()]
        c.execute("SELECT fact_type, fact_text, confidence FROM facts WHERE video_id=?", (vid,))
        v["key_facts"] = [{"type": r[0], "text": r[1], "confidence": r[2]} for r in c.fetchall()]
        for k in ("raw_json", "qa_analysis"): v.pop(k, None)
        videos.append(v)
    c.execute("SELECT entity, entity_type, video_id, context FROM entity_index ORDER BY entity")
    em = defaultdict(list)
    for ent, et, vid, ctx in c.fetchall(): em[ent].append({"type": et, "video_id": vid, "context": ctx})
    c.execute("SELECT semantic_topics FROM videos WHERE semantic_topics != ''")
    tc = Counter()
    for r in c.fetchall():
        for t in r[0].split(","): t = t.strip(); tc[t] += 1 if t else 0
    conn.close()
    kb = {"metadata": {"generated": datetime.datetime.now().isoformat(), "total_videos": len(videos),
          "copyright": __copyright__,
          "description": "YouTube knowledge base. Use entity_index for company/person lookups. Use chunks for transcript search. Use key_facts for claims/numbers."},
          "topic_clusters": dict(tc.most_common(30)), "entity_index": dict(em), "videos": videos}
    with open(filepath, "w", encoding="utf-8") as f: json.dump(kb, f, ensure_ascii=False, indent=1)
    return len(videos)

def export_claude_markdown(filepath):
    conn = sqlite3.connect(DB_FILE); c = conn.cursor(); stats = db_get_stats()
    c.execute("SELECT video_id, title, url, channel, category, quality_score, core_idea, actionable_insight, technologies, semantic_topics, mentioned_companies FROM videos ORDER BY quality_score DESC")
    videos = c.fetchall()
    c.execute("SELECT entity, entity_type, GROUP_CONCAT(video_id, ', ') FROM entity_index GROUP BY entity ORDER BY entity")
    entities = c.fetchall()
    c.execute("SELECT f.fact_text, f.fact_type, f.confidence, v.title FROM facts f JOIN videos v ON f.video_id=v.video_id ORDER BY f.confidence DESC")
    facts = c.fetchall(); conn.close()
    lines = ["# YouTube Knowledge Base", f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"Videos: {stats['total']} | Channels: {stats['channels']} | Avg: {stats['avg_score']}/10", "",
             "## Entity index", ""]
    for ent, et, vids in entities: lines.append(f"- **{ent}** ({et}): {vids}")
    lines += ["", "## Key facts", ""]
    for text, ft, conf, vt in facts[:100]: lines.append(f"- [{ft},{conf}] {text} (source: {vt})")
    lines += ["", "## Videos", ""]
    for vid, t, url, ch, cat, sc, idea, ins, tech, top, comp in videos:
        lines += [f"### {t}", f"{ch} | {cat} | {sc}/10 | {url}", f"Idea: {idea}", f"Insight: {ins}"]
        if tech: lines.append(f"Tech: {tech}")
        if comp: lines.append(f"Companies: {comp}")
        lines.append("")
    with open(filepath, "w", encoding="utf-8") as f: f.write("\n".join(lines))
    return len(videos)

def export_kb_summary(filepath):
    conn = sqlite3.connect(DB_FILE); c = conn.cursor(); stats = db_get_stats()
    c.execute("SELECT category, COUNT(*), AVG(quality_score) FROM videos GROUP BY category ORDER BY COUNT(*) DESC")
    cats = c.fetchall()
    c.execute("SELECT channel, COUNT(*), AVG(quality_score) FROM videos GROUP BY channel ORDER BY AVG(quality_score) DESC")
    chs = c.fetchall()
    c.execute("SELECT COUNT(*) FROM facts"); fc = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM chunks"); cc = c.fetchone()[0]
    c.execute("SELECT entity, entity_type, COUNT(*) FROM entity_index GROUP BY entity ORDER BY COUNT(*) DESC LIMIT 30")
    ents = c.fetchall(); conn.close()
    lines = ["# Knowledge Base Summary", f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             f"Videos: {stats['total']} | Channels: {stats['channels']} | Chunks: {cc} | Facts: {fc} | Entities: {len(ents)}+", "",
             "## Categories"]
    for cat, cnt, avg in cats: lines.append(f"- {cat}: {cnt} videos ({round(avg,1)}/10)")
    lines += ["", "## Channels"]
    for ch, cnt, avg in chs[:15]: lines.append(f"- {ch}: {cnt} videos, avg {round(avg,1)}/10")
    lines += ["", "## Top entities"]
    for ent, et, cnt in ents: lines.append(f"- {ent} ({et}): {cnt} videos")
    with open(filepath, "w", encoding="utf-8") as f: f.write("\n".join(lines))
    return stats["total"]

 #-------------

#url

def extract_video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else url.strip()

def is_channel_url(url):
    return bool(re.search(r"youtube\.com/(@|channel/|c/|user/)", url))


#apify scrape

def clean_channel_url(url):
    """Clean channel URL - remove /videos, /shorts etc. suffixes."""
    url = url.strip().rstrip("/")
    url = re.sub(r"/(videos|shorts|streams|playlists|community|about|featured|channels)$", "", url)
    return url

def apify_scrape(url, max_videos, log):
    try:
        from apify_client import ApifyClient
    except ImportError:
        log("[ERROR] apify-client not installed"); return []
    if not APIFY_API_TOKEN or "YOUR" in APIFY_API_TOKEN:
        log("[ERROR] APIFY_API_TOKEN not set"); return []

    client = ApifyClient(APIFY_API_TOKEN)
    is_channel = is_channel_url(url)
    original_url = url
    if is_channel:
        url = clean_channel_url(url)
    log(f"URL type: {'CHANNEL' if is_channel else 'VIDEO'}")
    log(f"Clean URL: {url}")
    log(f"Apify starting, max {max_videos}...")

    # For channels, try the dedicated channel scraper approach
    # Use the /videos page explicitly as startUrl for channel crawling
    if is_channel:
        channel_videos_url = url.rstrip("/") + "/videos"
        run_input = {
            "startUrls": [{"url": channel_videos_url}],
            "maxResults": max_videos,
            "maxResultsShorts": 0,
            "maxResultStreams": 0,
            "downloadSubtitles": True,
            "subtitlesLanguage": "any",
            "subtitlesFormat": "plaintext",
            "downloadComments": False,
            "maxComments": 0,
            "proxy": {"useApifyProxy": True},
        }
    else:
        run_input = {
            "startUrls": [{"url": url}],
            "maxResults": max_videos,
            "downloadSubtitles": True,
            "subtitlesLanguage": "any",
            "subtitlesFormat": "plaintext",
            "downloadComments": False,
            "maxComments": 0,
            "proxy": {"useApifyProxy": True},
        }

    log(f"Apify input URLs: {[u['url'] for u in run_input['startUrls']]}")

    try:
        run = client.actor("streamers/youtube-scraper").call(run_input=run_input)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        log(f"Apify returned: {len(items)} video(s)")

        # If channel returned very few results, try alternate actor
        if is_channel and len(items) < min(max_videos, 10):
            log(f"[WARN] Only {len(items)} videos for channel, trying alternate approach...")
            try:
                run_input2 = {
                    "startUrls": [{"url": url}],
                    "maxResults": max_videos,
                    "maxResultsShorts": 0,
                    "downloadSubtitles": True,
                    "subtitlesLanguage": "any",
                    "subtitlesFormat": "plaintext",
                    "downloadComments": False,
                    "maxComments": 0,
                    "proxy": {"useApifyProxy": True},
                }
                run2 = client.actor("bernardo/youtube-scraper").call(run_input=run_input2)
                items2 = list(client.dataset(run2["defaultDatasetId"]).iterate_items())
                log(f"Alternate actor returned: {len(items2)} video(s)")
                if len(items2) > len(items):
                    items = items2
            except Exception as e2:
                log(f"[WARN] Alternate actor failed: {e2}, using original {len(items)} results")

        return items
    except Exception as e:
        log(f"[ERROR] Apify: {e}"); return []

#apify scrape for batch
def apify_scrape_batch(urls, log):
    try:
        from apify_client import ApifyClient
    except ImportError:
        log("[ERROR] apify-client not installed"); return []
    if not APIFY_API_TOKEN or "YOUR" in APIFY_API_TOKEN:
        log("[ERROR] APIFY_API_TOKEN not set"); return []
    client = ApifyClient(APIFY_API_TOKEN)
    # Clean channel URLs
    cleaned = [clean_channel_url(u) if is_channel_url(u) else u for u in urls]
    log(f"Batch scraping {len(cleaned)} URLs...")
    run_input = {
        "startUrls": [{"url": u} for u in cleaned],
        "maxResults": len(cleaned),
        "maxResultsShorts": 0,
        "downloadSubtitles": True,
        "subtitlesLanguage": "any",
        "subtitlesFormat": "plaintext",
        "downloadComments": False,
        "maxComments": 0,
        "proxy": {"useApifyProxy": True},
    }
    try:
        run = client.actor("streamers/youtube-scraper").call(run_input=run_input)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        log(f"Apify: {len(items)} video(s)"); return items
    except Exception as e:
        log(f"[ERROR] Apify: {e}"); return []

#transcript
def get_transcript_text(item):
    subtitles = item.get("subtitles") or []
    if subtitles and isinstance(subtitles, list):
        first = subtitles[0] if subtitles else {}
        if isinstance(first, dict):
            pt = first.get("plaintext") or first.get("plain_text") or ""
            if pt and pt.strip(): return pt.strip()
            parts = [s.get("text") or s.get("content") or s.get("plaintext") or "" for s in subtitles if isinstance(s, dict)]
            joined = " ".join(p for p in parts if p).strip()
            if joined: return joined
        elif isinstance(first, str):
            joined = " ".join(s for s in subtitles if isinstance(s, str)).strip()
            if joined: return joined
    captions = item.get("captions") or []
    if captions and isinstance(captions, list):
        first = captions[0] if isinstance(captions[0], dict) else {}
        pt = first.get("plaintext") or first.get("text") or ""
        if pt: return pt.strip()
    for key in ("transcript", "subtitleText", "captionText", "text"):
        val = item.get(key)
        if val and isinstance(val, str) and val.strip(): return val.strip()
    desc = item.get("description") or ""
    if desc and len(desc) > 100: return f"[No subtitles]\n{desc.strip()}"
    return ""

#chunk transcript
def chunk_transcript(transcript, chunk_size=CHUNK_SIZE):
    if not transcript: return []
    words = transcript.split(); chunks = []; current = []; cl = 0
    for word in words:
        current.append(word); cl += len(word) + 1
        if cl >= chunk_size:
            chunks.append({"content": " ".join(current)}); current = []; cl = 0
    if current: chunks.append({"content": " ".join(current)})
    return chunks


# ── JSON repair helper ──

def repair_json_string(raw_json_str):
    """Fix common JSON issues from LLM output before parsing."""
    s = raw_json_str
    # Remove trailing commas before } or ]
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    # Fix unescaped newlines inside string values
    s = re.sub(r'(?<=": ")(.*?)(?="[,\s\n]*["\}\]])', lambda m: m.group(0).replace('\n', '\\n'), s)
    # Fix common escape issues - unescaped quotes inside strings
    # Replace \" that are not already escaped
    # Remove any control characters
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', s)
    return s

def safe_parse_json(raw_text, log=None):
    """Robustly parse JSON from Claude's response with multiple fallback strategies."""
    if not raw_text:
        return {}

    # Step 1: Clean markdown fences
    clean = re.sub(r"```(?:json)?|```", "", raw_text).strip()

    # Step 2: Extract JSON object
    m = re.search(r"\{.*\}", clean, re.DOTALL)
    if not m:
        if log:
            log("  [WARN] No JSON object found in response")
        return {}

    json_str = m.group()

    # Step 3: Try direct parse first
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Step 4: Try with trailing comma fix
    try:
        repaired = repair_json_string(json_str)
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Step 5: Aggressive repair - rebuild key by key
    try:
        # Try to fix by removing problematic trailing content
        # Find the last valid closing brace
        depth = 0
        last_valid = -1
        in_string = False
        escape_next = False
        for i, ch in enumerate(json_str):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    last_valid = i
                    break
        if last_valid > 0:
            truncated = json_str[:last_valid + 1]
            truncated = repair_json_string(truncated)
            return json.loads(truncated)
    except json.JSONDecodeError:
        pass

    # Step 6: Last resort - extract fields with regex
    try:
        data = {}
        # Extract simple string fields
        for field in ["summary", "category", "difficulty", "core_idea", "insight",
                      "audience", "creator", "company_info", "financial", "takeaway",
                      "q1", "a1", "q2", "a2", "q3", "a3"]:
            pm = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.){{0,2000}})"', json_str)
            if pm:
                data[field] = pm.group(1).replace('\\"', '"').replace('\\n', '\n')

        # Extract score
        pm = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', json_str)
        if pm:
            data["score"] = float(pm.group(1))

        # Extract arrays
        for field in ["companies", "tech", "tags", "semantic_topics"]:
            pm = re.search(rf'"{field}"\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
            if pm:
                items = re.findall(r'"((?:[^"\\]|\\.)*)"', pm.group(1))
                data[field] = items

        # Extract key_facts array
        pm = re.search(r'"key_facts"\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
        if pm:
            facts = []
            for fm in re.finditer(r'\{[^}]*?"type"\s*:\s*"([^"]*)"[^}]*?"text"\s*:\s*"((?:[^"\\]|\\.)*)"[^}]*?"confidence"\s*:\s*"([^"]*)"[^}]*?\}', pm.group(1)):
                facts.append({"type": fm.group(1), "text": fm.group(2), "confidence": fm.group(3)})
            if facts:
                data["key_facts"] = facts

        # Extract entities array
        pm = re.search(r'"entities"\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
        if pm:
            entities = []
            for em2 in re.finditer(r'\{[^}]*?"name"\s*:\s*"((?:[^"\\]|\\.)*)"[^}]*?"type"\s*:\s*"([^"]*)"[^}]*?"context"\s*:\s*"((?:[^"\\]|\\.)*)"[^}]*?\}', pm.group(1)):
                entities.append({"name": em2.group(1), "type": em2.group(2), "context": em2.group(3)})
            if entities:
                data["entities"] = entities

        if data:
            if log:
                log("  [WARN] Used regex fallback for JSON parsing")
            return data
    except Exception:
        pass

    if log:
        log("  [WARN] All JSON parse attempts failed")
    return {}


#claude

def call_claude(system_prompt, user_prompt, log, max_tokens=1800):
    try:
        import anthropic
    except ImportError:
        log("[ERROR] anthropic not installed"); return ""
    if not ANTHROPIC_API_KEY or "YOUR" in ANTHROPIC_API_KEY:
        log("[ERROR] ANTHROPIC_API_KEY not set"); return ""
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=max_tokens,
            system=system_prompt, messages=[{"role": "user", "content": user_prompt}])
        return msg.content[0].text.strip()
    except Exception as e:
        log(f"[ERROR] Claude: {e}"); return ""

ANALYSIS_SYSTEM = """You are a knowledge base builder for YouTube business intelligence.
Extract ALL useful information into structured JSON. Return ONLY valid JSON.
No markdown, no backticks. All values in ENGLISH. Ignore ads/sponsorships."""

def build_analysis_prompt(title, channel, transcript, extra):
    trimmed = transcript[:5000]
    extra_block = f"\nExtra: {extra}" if extra.strip() else ""
    return f"""Title: {title}\nChannel: {channel}{extra_block}\n\n<t>{trimmed}</t>\n\nReturn ONLY this JSON:
{{"summary":"<3 sentences>","q1":"<question>","a1":"<answer>","q2":"<question>","a2":"<answer>","q3":"<question>","a3":"<answer>","takeaway":"<1 actionable sentence>","category":"AI|SaaS|Business|Technology|Finance|Marketing|Education|Other","difficulty":"Beginner|Intermediate|Advanced","score":<1-10>,"core_idea":"<1-2 sentences>","insight":"<1 concrete lesson>","audience":"<who>","creator":"<info>","companies":["c1","c2"],"company_info":"<or None>","financial":"<or None>","tech":["t1","t2"],"tags":["t1","t2","t3","t4","t5"],"semantic_topics":["topic1","topic2","topic3"],"key_facts":[{{"type":"claim","text":"<claim>","confidence":"high|medium|low"}},{{"type":"statistic","text":"<number>","confidence":"high|medium|low"}}],"entities":[{{"name":"<name>","type":"company|person|tool|framework","context":"<how mentioned>"}}]}}"""

RECOMMEND_SYSTEM = """You are a BI advisor. Given YouTube data, suggest trends and gaps. Concise. English. Max 300 words."""
def build_recommend_prompt(s):
    return f"Data:\n{s}\n\n1. Top 3 content gaps\n2. Top 3 trending tech\n3. Top 3 channels\n4. One surprise"


#file writer

def write_transcript(path, meta, transcript):
    mode = "a" if os.path.exists(path) else "w"
    with open(path, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write(f"# Transcripts\n*{__copyright__}*\n*Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
        f.write(f"\n\n{'='*70}\n# {meta['title']}\nURL: {meta['url']}\nChannel: {meta['channel']}\n")
        f.write(f"Published: {meta['published']} | Duration: {meta['duration']} | Views: {meta['views']}\n\n---\n\n")
        f.write(transcript if transcript else "*No subtitles*\n")

#question
def write_qa(path, meta, data):
    mode = "a" if os.path.exists(path) else "w"
    with open(path, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write(f"# Q&A Analysis\n*{__copyright__}*\n*Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
        f.write(f"\n\n{'='*70}\n# {meta['title']}\nURL: {meta['url']}\nChannel: {meta['channel']}\n\n---\n\n")
        if data:
            f.write(f"## Summary\n{data.get('summary','N/A')}\n\n")
            for i in range(1,4): f.write(f"**Q{i}:** {data.get(f'q{i}','')}\n**A{i}:** {data.get(f'a{i}','')}\n\n")
            f.write(f"## Takeaway\n{data.get('takeaway','N/A')}\n")
            facts = data.get("key_facts", [])
            if facts:
                f.write("\n## Key Facts\n")
                for fact in facts: f.write(f"- [{fact.get('type','')}] {fact.get('text','')} ({fact.get('confidence','')})\n")
        else:
            f.write("*Analysis not available*\n")

#row
def write_csv_row(path, row):
    #headers
    cols = [k for k in row.keys() if k not in ("transcript", "qa_analysis", "raw_json")]
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if not exists: w.writeheader()
        w.writerow({k: row.get(k, "") for k in cols})

#dashboard
def generate_dashboard(out_dir, name, log):
    stats = db_get_stats()
    if stats["total"] == 0: return

    conn = sqlite3.connect(DB_FILE); c = conn.cursor()

    # ── Gather ALL analytics data ──
    c.execute("SELECT title, url, category, quality_score, difficulty, channel, views, duration, published, technologies, auto_tags, semantic_topics, mentioned_companies, core_idea FROM videos ORDER BY processed_at DESC")
    rows = c.fetchall()
    c.execute("SELECT category, COUNT(*), AVG(quality_score) FROM videos GROUP BY category ORDER BY COUNT(*) DESC")
    cat_rows = c.fetchall()
    c.execute("SELECT COUNT(*) FROM facts"); fc = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM chunks"); cc = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT entity) FROM entity_index"); ec = c.fetchone()[0]
    c.execute("SELECT difficulty, COUNT(*) FROM videos WHERE difficulty != '' GROUP BY difficulty ORDER BY COUNT(*) DESC")
    diff_rows = c.fetchall()
    c.execute("SELECT channel, COUNT(*), AVG(quality_score), GROUP_CONCAT(DISTINCT category) FROM videos GROUP BY channel ORDER BY AVG(quality_score) DESC")
    ch_rows = c.fetchall()
    c.execute("SELECT entity, entity_type, COUNT(*) as cnt FROM entity_index GROUP BY entity ORDER BY cnt DESC LIMIT 25")
    top_entities = c.fetchall()
    c.execute("SELECT entity_type, COUNT(DISTINCT entity) FROM entity_index GROUP BY entity_type ORDER BY COUNT(DISTINCT entity) DESC")
    entity_types = c.fetchall()
    c.execute("SELECT fact_type, COUNT(*) FROM facts GROUP BY fact_type ORDER BY COUNT(*) DESC")
    fact_types = c.fetchall()
    c.execute("SELECT f.fact_text, f.fact_type, f.confidence, v.title FROM facts f JOIN videos v ON f.video_id=v.video_id WHERE f.confidence='high' ORDER BY RANDOM() LIMIT 15")
    top_facts = c.fetchall()
    c.execute("SELECT quality_score FROM videos WHERE quality_score > 0")
    all_scores = [r[0] for r in c.fetchall()]
    c.execute("SELECT title, url, channel, quality_score, category FROM videos WHERE quality_score > 0 ORDER BY quality_score DESC LIMIT 10")
    top_videos = c.fetchall()
    c.execute("SELECT title, url, channel, quality_score, category FROM videos WHERE quality_score > 0 ORDER BY quality_score ASC LIMIT 5")
    bottom_videos = c.fetchall()
    c.execute("SELECT semantic_topics FROM videos WHERE semantic_topics != ''")
    topic_counter = Counter()
    for r in c.fetchall():
        for t in r[0].split(","):
            t = t.strip()
            if t: topic_counter[t] += 1
    top_topics = topic_counter.most_common(20)
    c.execute("SELECT auto_tags FROM videos WHERE auto_tags != ''")
    tag_counter = Counter()
    for r in c.fetchall():
        for t in r[0].split(","):
            t = t.strip()
            if t: tag_counter[t] += 1
    top_tags = tag_counter.most_common(30)
    c.execute("SELECT mentioned_companies FROM videos WHERE mentioned_companies != ''")
    comp_counter = Counter()
    for r in c.fetchall():
        for cm in r[0].split(","):
            cm = cm.strip()
            if cm: comp_counter[cm] += 1
    top_companies = comp_counter.most_common(20)

    # Score distribution
    score_dist = Counter()
    for s in all_scores: score_dist[int(s)] = score_dist.get(int(s), 0) + 1

    # Difficulty per category
    c.execute("SELECT category, difficulty, COUNT(*) FROM videos WHERE difficulty != '' AND category != '' GROUP BY category, difficulty")
    cat_diff = defaultdict(dict)
    for cat, diff, cnt in c.fetchall(): cat_diff[cat][diff] = cnt

    conn.close()

    total = stats["total"]
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    avg_score = stats["avg_score"]
    max_score = max(all_scores) if all_scores else 0
    min_score = min(all_scores) if all_scores else 0
    median_score = sorted(all_scores)[len(all_scores)//2] if all_scores else 0

    # ── Helper: escape HTML ──
    def h(text):
        return str(text).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

    # ── Build bar chart SVG helper ──
    def bar_chart_svg(data, max_val, width=320, bar_h=22, color="#fff"):
        if not data: return ""
        svg_h = len(data) * (bar_h + 6) + 10
        lines = [f'<svg width="{width}" height="{svg_h}" xmlns="http://www.w3.org/2000/svg">']
        for i, (label, val) in enumerate(data):
            y = i * (bar_h + 6) + 4
            bw = (val / max_val * (width - 140)) if max_val else 0
            lines.append(f'<text x="0" y="{y+15}" fill="#888" font-size="11" font-family="monospace">{h(str(label)[:18])}</text>')
            lines.append(f'<rect x="130" y="{y}" width="{max(bw,2)}" height="{bar_h}" rx="3" fill="{color}" opacity="0.85"/>')
            lines.append(f'<text x="{130+bw+6}" y="{y+15}" fill="#aaa" font-size="11" font-family="monospace">{val}</text>')
        lines.append('</svg>')
        return "\n".join(lines)

    # ── Build HTML ──
    html_parts = []
    html_parts.append(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(name)} — Analytics Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#0a0a0a;--card:#111;--card2:#161616;--border:#222;--accent:#fff;--muted:#666;--dim:#444;--text:#ccc;--green:#2a5;--red:#c44;--blue:#48f;--yellow:#da3;--purple:#a6f}}
body{{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;padding:0;line-height:1.5}}
a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}
.wrap{{max-width:1400px;margin:0 auto;padding:20px}}
.header{{background:#111;border-bottom:1px solid var(--border);padding:28px 32px;margin-bottom:24px}}
.header h1{{color:var(--accent);font-size:22px;letter-spacing:2px;text-transform:uppercase}}
.header .sub{{color:var(--muted);font-size:12px;margin-top:6px}}
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}}
.kpi{{background:var(--card);border:1px solid var(--border);padding:18px 16px;text-align:center}}
.kpi .val{{font-size:28px;font-weight:bold;color:var(--accent);letter-spacing:1px}}
.kpi .lbl{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;margin-top:4px}}
.kpi .delta{{font-size:10px;margin-top:2px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:20px}}
@media(max-width:900px){{.grid2,.grid3{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--border);padding:20px;margin-bottom:16px}}
.card h2{{font-size:11px;text-transform:uppercase;letter-spacing:3px;color:var(--muted);margin-bottom:14px;border-bottom:1px solid var(--border);padding-bottom:8px}}
.card h3{{font-size:10px;text-transform:uppercase;letter-spacing:2px;color:var(--dim);margin:12px 0 6px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:1px;padding:8px 10px;border-bottom:1px solid var(--border);font-weight:bold}}
td{{padding:7px 10px;border-bottom:1px solid #1a1a1a;color:var(--text)}}
tr:hover td{{background:#1a1a1a}}
.bar-cell{{position:relative}}
.bar-fill{{position:absolute;left:0;top:0;bottom:0;background:var(--accent);opacity:0.07;border-radius:2px}}
.tag{{display:inline-block;background:#1a1a1a;border:1px solid var(--border);padding:3px 10px;margin:3px;font-size:11px;color:var(--text);border-radius:2px}}
.tag .cnt{{color:var(--muted);font-size:10px;margin-left:4px}}
.score-bar{{display:inline-block;height:8px;border-radius:1px;margin-right:6px;vertical-align:middle}}
.entity-grid{{display:flex;flex-wrap:wrap;gap:6px}}
.entity{{background:var(--card2);border:1px solid var(--border);padding:6px 12px;font-size:11px}}
.entity .etype{{color:var(--muted);font-size:9px;text-transform:uppercase}}
.fact-item{{padding:8px 0;border-bottom:1px solid #1a1a1a;font-size:12px}}
.fact-item .ftype{{font-size:9px;text-transform:uppercase;color:var(--yellow);margin-right:6px}}
.fact-item .fsrc{{color:var(--dim);font-size:10px;margin-top:2px}}
.vid-row{{padding:8px 0;border-bottom:1px solid #1a1a1a}}
.vid-row .vtitle{{font-size:12px;color:var(--accent)}}
.vid-row .vmeta{{font-size:10px;color:var(--muted);margin-top:2px}}
.score-badge{{display:inline-block;padding:2px 8px;font-size:11px;font-weight:bold;border-radius:2px;margin-right:6px}}
.s-high{{background:#1a3a1a;color:#4c8;border:1px solid #2a5a2a}}
.s-mid{{background:#3a3a1a;color:#cc8;border:1px solid #5a5a2a}}
.s-low{{background:#3a1a1a;color:#c66;border:1px solid #5a2a2a}}
.heatmap{{display:grid;grid-template-columns:repeat(11,1fr);gap:3px;margin:8px 0}}
.hm-cell{{text-align:center;padding:8px 2px;font-size:11px;border-radius:2px}}
.footer{{text-align:center;color:var(--dim);font-size:10px;padding:24px;border-top:1px solid var(--border);margin-top:24px}}
</style></head><body>
<div class="header">
<h1>{h(__app_name__)} &mdash; {h(name)}</h1>
<div class="sub">{now_str} &nbsp;|&nbsp; {h(__copyright__)}</div>
</div>
<div class="wrap">
""")

    # ── KPI ROW ──
    html_parts.append(f"""<div class="kpi-row">
<div class="kpi"><div class="val">{total}</div><div class="lbl">Videos</div></div>
<div class="kpi"><div class="val">{stats['channels']}</div><div class="lbl">Channels</div></div>
<div class="kpi"><div class="val">{stats['categories']}</div><div class="lbl">Categories</div></div>
<div class="kpi"><div class="val">{avg_score}</div><div class="lbl">Avg Score /10</div></div>
<div class="kpi"><div class="val">{cc}</div><div class="lbl">Chunks</div></div>
<div class="kpi"><div class="val">{fc}</div><div class="lbl">Facts</div></div>
<div class="kpi"><div class="val">{ec}</div><div class="lbl">Entities</div></div>
<div class="kpi"><div class="val">{len(top_tags)}</div><div class="lbl">Unique Tags</div></div>
</div>""")

    # ── SCORE KPI ROW ──
    html_parts.append(f"""<div class="kpi-row">
<div class="kpi"><div class="val" style="color:var(--green)">{max_score}</div><div class="lbl">Best Score</div></div>
<div class="kpi"><div class="val" style="color:var(--yellow)">{median_score}</div><div class="lbl">Median Score</div></div>
<div class="kpi"><div class="val" style="color:var(--red)">{min_score}</div><div class="lbl">Lowest Score</div></div>
<div class="kpi"><div class="val">{len(comp_counter)}</div><div class="lbl">Companies</div></div>
<div class="kpi"><div class="val">{len(topic_counter)}</div><div class="lbl">Topics</div></div>
<div class="kpi"><div class="val">{sum(1 for s in all_scores if s >= 8)}</div><div class="lbl">Score 8+</div></div>
<div class="kpi"><div class="val">{sum(1 for s in all_scores if s < 5)}</div><div class="lbl">Score &lt;5</div></div>
<div class="kpi"><div class="val">{len(all_scores)}</div><div class="lbl">Scored Videos</div></div>
</div>""")

    # ── CATEGORY DISTRIBUTION + DIFFICULTY ──
    html_parts.append('<div class="grid2">')
    # Category table
    html_parts.append('<div class="card"><h2>Category Distribution</h2><table>')
    html_parts.append('<tr><th>Category</th><th>Videos</th><th>Share</th><th>Avg Score</th><th>Distribution</th></tr>')
    max_cat = cat_rows[0][1] if cat_rows else 1
    for cat, cnt, avgsc in cat_rows:
        pct = round(cnt/total*100,1) if total else 0
        bar_w = cnt/max_cat*100
        sc_color = "var(--green)" if (avgsc or 0)>=7 else ("var(--yellow)" if (avgsc or 0)>=5 else "var(--red)")
        html_parts.append(f'<tr><td><b>{h(cat)}</b></td><td>{cnt}</td><td>{pct}%</td><td style="color:{sc_color}">{round(avgsc or 0,1)}</td>')
        html_parts.append(f'<td class="bar-cell"><div class="bar-fill" style="width:{bar_w}%"></div>&nbsp;</td></tr>')
    html_parts.append('</table></div>')

    # Difficulty breakdown
    html_parts.append('<div class="card"><h2>Difficulty Breakdown</h2><table>')
    html_parts.append('<tr><th>Level</th><th>Count</th><th>Share</th><th></th></tr>')
    diff_colors = {"Beginner":"var(--green)","Intermediate":"var(--yellow)","Advanced":"var(--red)"}
    max_diff = diff_rows[0][1] if diff_rows else 1
    for diff, cnt in diff_rows:
        pct = round(cnt/total*100,1)
        clr = diff_colors.get(diff, "var(--muted)")
        html_parts.append(f'<tr><td style="color:{clr}"><b>{h(diff)}</b></td><td>{cnt}</td><td>{pct}%</td>')
        html_parts.append(f'<td><div style="background:{clr};width:{cnt/max_diff*100}%;height:10px;border-radius:2px;opacity:0.6"></div></td></tr>')
    html_parts.append('</table>')

    # Difficulty per category matrix
    if cat_diff:
        html_parts.append('<h3>Difficulty × Category</h3><table><tr><th>Category</th><th>Beginner</th><th>Intermediate</th><th>Advanced</th></tr>')
        for cat in sorted(cat_diff.keys()):
            dd = cat_diff[cat]
            html_parts.append(f'<tr><td>{h(cat)}</td><td>{dd.get("Beginner",0)}</td><td>{dd.get("Intermediate",0)}</td><td>{dd.get("Advanced",0)}</td></tr>')
        html_parts.append('</table>')
    html_parts.append('</div></div>')

    # ── TOP TECHNOLOGIES + SCORE HEATMAP ──
    html_parts.append('<div class="grid2">')
    html_parts.append('<div class="card"><h2>Top Technologies</h2><table>')
    html_parts.append('<tr><th>#</th><th>Technology</th><th>Mentions</th><th></th></tr>')
    max_tech = stats["top_tech"][0][1] if stats["top_tech"] else 1
    for i, (tech, cnt) in enumerate(stats["top_tech"], 1):
        bar_w = cnt/max_tech*100
        html_parts.append(f'<tr><td style="color:var(--muted)">{i}</td><td><b>{h(tech)}</b></td><td>{cnt}x</td>')
        html_parts.append(f'<td><div style="background:var(--blue);width:{bar_w}%;height:10px;border-radius:2px;opacity:0.5"></div></td></tr>')
    html_parts.append('</table></div>')

    # Score distribution heatmap
    html_parts.append('<div class="card"><h2>Score Distribution</h2>')
    html_parts.append('<div class="heatmap">')
    max_sd = max(score_dist.values()) if score_dist else 1
    for s in range(0, 11):
        cnt = score_dist.get(s, 0)
        intensity = cnt/max_sd if max_sd else 0
        r = int(40 + intensity*200)
        g = int(40 + intensity*80)
        bg = f"rgb({r},{g},40)" if cnt > 0 else "#1a1a1a"
        html_parts.append(f'<div class="hm-cell" style="background:{bg};color:{"#fff" if intensity>0.3 else "#666"}">{s}<br><small>{cnt}</small></div>')
    html_parts.append('</div>')

    # Score stats summary
    if all_scores:
        above7 = sum(1 for s in all_scores if s >= 7)
        above8 = sum(1 for s in all_scores if s >= 8)
        below5 = sum(1 for s in all_scores if s < 5)
        html_parts.append(f'<h3>Score Analysis</h3><table>')
        html_parts.append(f'<tr><td>Score ≥ 8 (Excellent)</td><td style="color:var(--green)"><b>{above8}</b> ({round(above8/len(all_scores)*100,1)}%)</td></tr>')
        html_parts.append(f'<tr><td>Score ≥ 7 (Good+)</td><td style="color:var(--green)">{above7} ({round(above7/len(all_scores)*100,1)}%)</td></tr>')
        html_parts.append(f'<tr><td>Score &lt; 5 (Low)</td><td style="color:var(--red)">{below5} ({round(below5/len(all_scores)*100,1)}%)</td></tr>')
        html_parts.append(f'<tr><td>Standard Dev</td><td>{round((sum((s-avg_score)**2 for s in all_scores)/len(all_scores))**0.5, 2) if all_scores else 0}</td></tr>')
        html_parts.append('</table>')
    html_parts.append('</div></div>')

    # ── CHANNEL COMPARISON ──
    html_parts.append('<div class="card"><h2>Channel Comparison</h2><table>')
    html_parts.append('<tr><th>Channel</th><th>Videos</th><th>Avg Score</th><th>Categories</th><th>Performance</th></tr>')
    for ch_name_r, ch_cnt, ch_avg, ch_cats in ch_rows:
        sc_color = "var(--green)" if (ch_avg or 0)>=7 else ("var(--yellow)" if (ch_avg or 0)>=5 else "var(--red)")
        bar_w = (ch_avg or 0)/10*100
        html_parts.append(f'<tr><td><b>{h(ch_name_r)}</b></td><td>{ch_cnt}</td><td style="color:{sc_color}">{round(ch_avg or 0,1)}/10</td><td style="color:var(--muted)">{h((ch_cats or "")[:50])}</td>')
        html_parts.append(f'<td><div style="background:{sc_color};width:{bar_w}%;height:10px;border-radius:2px;opacity:0.5"></div></td></tr>')
    html_parts.append('</table></div>')

    # ── TOP VIDEOS + BOTTOM VIDEOS ──
    html_parts.append('<div class="grid2">')
    html_parts.append('<div class="card"><h2>Top 10 Videos (Highest Score)</h2>')
    for i, (vt, vu, vch, vsc, vcat) in enumerate(top_videos, 1):
        sc_cls = "s-high" if vsc >= 8 else ("s-mid" if vsc >= 6 else "s-low")
        html_parts.append(f'<div class="vid-row"><span class="score-badge {sc_cls}">{vsc}</span><a href="{h(vu)}" class="vtitle">{h(vt)}</a><div class="vmeta">{h(vch)} &bull; {h(vcat)}</div></div>')
    html_parts.append('</div>')

    html_parts.append('<div class="card"><h2>Bottom 5 Videos (Lowest Score)</h2>')
    for i, (vt, vu, vch, vsc, vcat) in enumerate(bottom_videos, 1):
        sc_cls = "s-low" if vsc < 5 else ("s-mid" if vsc < 7 else "s-high")
        html_parts.append(f'<div class="vid-row"><span class="score-badge {sc_cls}">{vsc}</span><a href="{h(vu)}" class="vtitle">{h(vt)}</a><div class="vmeta">{h(vch)} &bull; {h(vcat)}</div></div>')
    html_parts.append('</div></div>')

    # ── TOP COMPANIES + ENTITY TYPES ──
    html_parts.append('<div class="grid2">')
    html_parts.append('<div class="card"><h2>Most Mentioned Companies</h2><table>')
    html_parts.append('<tr><th>#</th><th>Company</th><th>Mentions</th><th></th></tr>')
    max_comp = top_companies[0][1] if top_companies else 1
    for i, (comp, cnt) in enumerate(top_companies[:15], 1):
        bar_w = cnt/max_comp*100
        html_parts.append(f'<tr><td style="color:var(--muted)">{i}</td><td><b>{h(comp)}</b></td><td>{cnt}x</td>')
        html_parts.append(f'<td><div style="background:var(--purple);width:{bar_w}%;height:10px;border-radius:2px;opacity:0.5"></div></td></tr>')
    html_parts.append('</table></div>')

    html_parts.append('<div class="card"><h2>Entity Types</h2><table>')
    html_parts.append('<tr><th>Type</th><th>Unique Entities</th><th></th></tr>')
    max_et = entity_types[0][1] if entity_types else 1
    for etype, ecnt in entity_types:
        bar_w = ecnt/max_et*100
        html_parts.append(f'<tr><td><b>{h(etype)}</b></td><td>{ecnt}</td>')
        html_parts.append(f'<td><div style="background:var(--yellow);width:{bar_w}%;height:10px;border-radius:2px;opacity:0.4"></div></td></tr>')
    html_parts.append('</table>')

    html_parts.append('<h3>Fact Types</h3><table><tr><th>Type</th><th>Count</th></tr>')
    for ft, ftc in fact_types:
        html_parts.append(f'<tr><td>{h(ft)}</td><td>{ftc}</td></tr>')
    html_parts.append('</table></div></div>')

    # ── TOP ENTITIES (grid) ──
    html_parts.append('<div class="card"><h2>Top 25 Entities</h2><div class="entity-grid">')
    for ent, etype, ecnt in top_entities:
        html_parts.append(f'<div class="entity"><b>{h(ent)}</b> <span class="etype">{h(etype)}</span> <span style="color:var(--muted)">({ecnt})</span></div>')
    html_parts.append('</div></div>')

    # ── SEMANTIC TOPICS (tag cloud) ──
    html_parts.append('<div class="grid2">')
    html_parts.append('<div class="card"><h2>Semantic Topics</h2><div>')
    max_topic = top_topics[0][1] if top_topics else 1
    for topic, cnt in top_topics:
        size = 11 + int((cnt/max_topic)*8)
        html_parts.append(f'<span class="tag" style="font-size:{size}px">{h(topic)}<span class="cnt">{cnt}</span></span>')
    html_parts.append('</div></div>')

    # Tags cloud
    html_parts.append('<div class="card"><h2>Auto Tags</h2><div>')
    max_tag = top_tags[0][1] if top_tags else 1
    for tag, cnt in top_tags:
        size = 10 + int((cnt/max_tag)*7)
        html_parts.append(f'<span class="tag" style="font-size:{size}px">{h(tag)}<span class="cnt">{cnt}</span></span>')
    html_parts.append('</div></div></div>')

    # ── KEY FACTS ──
    if top_facts:
        html_parts.append('<div class="card"><h2>Key Facts (High Confidence)</h2>')
        for ftext, ftype, fconf, ftitle in top_facts:
            html_parts.append(f'<div class="fact-item"><span class="ftype">{h(ftype)}</span>{h(ftext)}<div class="fsrc">Source: {h(ftitle)}</div></div>')
        html_parts.append('</div>')

    # ── ALL VIDEOS TABLE ──
    html_parts.append('<div class="card"><h2>All Videos</h2><table>')
    html_parts.append('<tr><th>#</th><th>Title</th><th>Channel</th><th>Category</th><th>Score</th><th>Difficulty</th></tr>')
    for i, row in enumerate(rows, 1):
        t, u, cat, sc, d, ch = row[0], row[1], row[2], row[3], row[4], row[5]
        sc_color = "var(--green)" if (sc or 0)>=8 else ("var(--yellow)" if (sc or 0)>=6 else ("var(--red)" if (sc or 0)>0 else "var(--dim)"))
        html_parts.append(f'<tr><td style="color:var(--dim)">{i}</td><td><a href="{h(u)}">{h((t or "?")[:80])}</a></td>')
        html_parts.append(f'<td>{h(ch)}</td><td>{h(cat)}</td><td style="color:{sc_color}"><b>{sc or "-"}</b></td><td>{h(d)}</td></tr>')
    html_parts.append('</table></div>')

    # ── FOOTER ──
    html_parts.append(f"""
<div class="footer">
{h(__app_name__)} v{h(__version__)} &mdash; {h(__copyright__)}<br>
Generated {now_str} &nbsp;|&nbsp; {total} videos &nbsp;|&nbsp; {stats['channels']} channels &nbsp;|&nbsp; {fc} facts &nbsp;|&nbsp; {ec} entities
</div></div></body></html>""")

    # Write HTML
    html_path = os.path.join(out_dir, f"{safe_name(name)}_Dashboard.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))
    log(f"Dashboard HTML: {name}")

    # Also keep MD (lightweight)
    md_lines = [f"# {name} — Dashboard", f"*{__copyright__}*", "",
        f"> {now_str} | Videos: {total} | Channels: {stats['channels']} | Avg: {avg_score}/10",
        f"> KB: {cc} chunks, {fc} facts, {ec} entities", "", "## Categories", "",
        "| Category | Count | Share |", "|----------|-------|-------|"]
    for cat, cnt, avgsc in cat_rows:
        pct = round(cnt/total*100,1) if total else 0
        md_lines.append(f"| {cat} | {cnt} | {pct}% |")
    md_lines += ["", "## Top Tech", "", "| # | Tech | Mentions |", "|---|------|----------|"]
    for i, (tech, cnt) in enumerate(stats["top_tech"], 1): md_lines.append(f"| {i} | {tech} | {cnt}x |")
    md_lines += ["", "## Videos", ""]
    for row in rows: md_lines.append(f"- [{row[0]}]({row[1]}) — {row[2]} | {row[3]}/10 | {row[4]} | {row[5]}")
    with open(os.path.join(out_dir, f"{safe_name(name)}_Dashboard.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    log(f"Dashboard MD: {name}")


#single videdo

def process_video(item, extra_instructions, project_category, log):
    #in All video
    title = item.get("title") or "Untitled"
    vid_url = item.get("url") or item.get("id") or ""
    channel = item.get("channelName") or item.get("channel") or ""
    pub = item.get("publishedAt") or item.get("uploadDate") or ""
    dur = str(item.get("duration") or "")
    views = str(item.get("viewCount") or item.get("views") or "")
    lang = item.get("language") or "auto"
    vid_id = extract_video_id(vid_url)

    # history check
    status = history_check(vid_id)
    if status == "done":
        log(f"  Skip (already in history)")
        return False

    # folder path
    ch_dir = get_channel_dir(project_category, channel)    # Scraper/Category/Channel/
    all_dir = get_all_dir()                                 # Scraper/_All/
    ch_name = safe_name(channel)

    # name after chan
    ch_transcript = get_channel_file(ch_dir, channel, "_Transcripts.md")
    ch_qa = get_channel_file(ch_dir, channel, "_QA.md")
    ch_csv = get_channel_file(ch_dir, channel, ".csv")

    # in _All
    all_transcript = get_all_file("_Transcripts.md")
    all_qa = get_all_file("_QA.md")
    all_csv = get_all_file(".csv")

    try:
        transcript = get_transcript_text(item)
        if not transcript: log("  No subtitles")

        meta = {"title": title, "url": vid_url, "channel": channel,
                "published": pub, "duration": dur, "views": views}

        # Write transcript to both channel folder and _All
        write_transcript(ch_transcript, meta, transcript)
        write_transcript(all_transcript, meta, transcript)

        # Chunk transcript
        chunks = chunk_transcript(transcript)
        if chunks: db_insert_chunks(vid_id, chunks)

        # Claude analysis
        log("  Claude analyzing...")
        raw = call_claude(ANALYSIS_SYSTEM,
            build_analysis_prompt(title, channel, transcript, extra_instructions),
            log, max_tokens=1400)

        # ── FIXED: robust JSON parsing with repair ──
        data = safe_parse_json(raw, log)

        # Write Q&A to both folders
        write_qa(ch_qa, meta, data)
        write_qa(all_qa, meta, data)

        # Extract and store facts
        facts = data.get("key_facts", [])
        if facts and isinstance(facts, list): db_insert_facts(vid_id, facts)

        # Extract and store entities
        entities = data.get("entities", [])
        if entities and isinstance(entities, list): db_insert_entities(vid_id, entities)

        # Build row
        companies = data.get("companies", [])
        techs = data.get("tech", [])
        tags = data.get("tags", [])
        topics = data.get("semantic_topics", [])

        row = {
            "video_id": vid_id, "title": title, "url": vid_url,
            "channel": channel, "published": pub, "duration": dur, "views": views,
            "category": data.get("category", ""), "difficulty": data.get("difficulty", ""),
            "quality_score": data.get("score", 0), "core_idea": data.get("core_idea", ""),
            "actionable_insight": data.get("insight", ""), "target_audience": data.get("audience", ""),
            "creator_info": data.get("creator", ""),
            "mentioned_companies": ", ".join(companies) if isinstance(companies, list) else str(companies),
            "company_details": data.get("company_info", ""), "financial_data": data.get("financial", ""),
            "technologies": ", ".join(techs) if isinstance(techs, list) else str(techs),
            "auto_tags": ", ".join(tags) if isinstance(tags, list) else str(tags),
            "semantic_topics": ", ".join(topics) if isinstance(topics, list) else str(topics),
            "language": lang, "processed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "project_category": project_category,
        }

        # Write CSV row to both channel and _All
        write_csv_row(ch_csv, row)
        write_csv_row(all_csv, row)

        # Database
        db_insert_video(row, transcript, json.dumps(data, ensure_ascii=False) if data else "", raw or "")
        db_update_channel_stats(channel)

        # Mark as done in history
        history_mark(vid_id, vid_url, channel, title, "done")
        log("  Done")
        return True

    except Exception as e:
        # Mark as failed in history (won't retry automatically)
        history_mark(vid_id, vid_url, channel, title, "failed")
        log(f"  [ERROR] {e}")
        return False


#pipelines

def run_pipeline(url, category, max_videos, auto_open, extra, log, set_progress, on_done):
    if not is_channel_url(url): max_videos = 1

    items = apify_scrape(url, max_videos, log)
    if not items: log("No videos extracted"); on_done(); return

    # filter Db
    all_history = history_get_all()
    new_items = [i for i in items if extract_video_id(i.get("url") or i.get("id") or "") not in all_history]
    skipped = len(items) - len(new_items)
    if skipped: log(f"Skipped {skipped} (already in history)")
    if not new_items: log("All videos already processed"); on_done(); return

    log(f"\n{len(new_items)} new video(s)\n")
    for idx, item in enumerate(new_items, 1):
        log(f"[{idx}/{len(new_items)}] {(item.get('title') or '?')[:60]}")
        set_progress(idx - 1, len(new_items))
        process_video(item, extra, category, log)
        set_progress(idx, len(new_items))

    # Dashboard _All
    generate_dashboard(get_all_dir(), ALL_FOLDER, log)
    log(f"\nDone! {db_get_video_count()} videos | {history_count()} in history\n")

    if auto_open:
        try:
            target = get_all_dir()
            if sys.platform == "win32": os.startfile(target)
            elif sys.platform == "darwin": subprocess.run(["open", target])
            else: subprocess.run(["xdg-open", target])
        except: pass
    on_done()

def run_bulk_pipeline(urls, category, auto_open, log, set_progress, on_done):
    items = apify_scrape_batch(urls, log)
    if not items: log("No videos extracted"); on_done(); return

    all_history = history_get_all()
    new_items = [i for i in items if extract_video_id(i.get("url") or i.get("id") or "") not in all_history]
    skipped = len(items) - len(new_items)
    if skipped: log(f"Skipped {skipped} (already in history)")
    if not new_items: log("All already processed"); on_done(); return

    log(f"\n{len(new_items)} new videos\n")
    for idx, item in enumerate(new_items, 1):
        log(f"[{idx}/{len(new_items)}] {(item.get('title') or '?')[:60]}")
        set_progress(idx - 1, len(new_items))
        process_video(item, "", category, log)
        set_progress(idx, len(new_items))

    generate_dashboard(get_all_dir(), ALL_FOLDER, log)
    log(f"\nDone! {db_get_video_count()} videos | {history_count()} in history\n")

    if auto_open:
        try:
            if sys.platform == "win32": os.startfile(get_all_dir())
            elif sys.platform == "darwin": subprocess.run(["open", get_all_dir()])
            else: subprocess.run(["xdg-open", get_all_dir()])
        except: pass
    on_done()


#Gui

BG="#0A0A0A"; PANEL="#141414"; CARD="#1A1A1A"; ACCENT="#FFFFFF"
DANGER="#CC3333"; TEXT="#E0E0E0"; MUTED="#666666"; BORDER="#2A2A2A"
HOVER="#222222"; DIM="#444444"

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{__app_name__} v{__version__}")
        self.configure(bg=BG); self.minsize(1000, 800)
        w, h = 1060, 850
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth()-w)//2}+{(self.winfo_screenheight()-h)//2}")

        self.notebook = ttk.Notebook(self); self.notebook.pack(fill="both", expand=True)
        style = ttk.Style(); style.theme_use("default")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=[14,8], font=("Helvetica",10))
        style.map("TNotebook.Tab", background=[("selected",CARD)], foreground=[("selected",ACCENT)])
        style.configure("D.Horizontal.TProgressbar", troughcolor=PANEL, background=DIM, thickness=10)
        style.configure("Treeview", background=PANEL, foreground=TEXT, fieldbackground=PANEL, borderwidth=0, font=("Helvetica",9))
        style.configure("Treeview.Heading", background=CARD, foreground=MUTED, font=("Helvetica",9,"bold"), borderwidth=0)
        style.map("Treeview", background=[("selected",BORDER)], foreground=[("selected",ACCENT)])

        self._build_scrape_tab(); self._build_bulk_tab(); self._build_search_tab()
        self._build_channels_tab(); self._build_insights_tab(); self._build_export_tab()

    def _build_scrape_tab(self):
        tab = tk.Frame(self.notebook, bg=BG); self.notebook.add(tab, text="  Scrape  ")
        hdr = tk.Frame(tab, bg=CARD, height=44); hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text=f"{__app_name__.upper()} v{__version__}", font=("Helvetica",11,"bold"), bg=CARD, fg=ACCENT, padx=16).pack(side="left", pady=10)
        self.db_lbl = tk.Label(hdr, text="", font=("Helvetica",9), bg=CARD, fg=MUTED); self.db_lbl.pack(side="right", padx=16)
        self._refresh_db()

        canvas = tk.Canvas(tab, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview); canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        self.sm = tk.Frame(canvas, bg=BG); wid = canvas.create_window((0,0), window=self.sm, anchor="nw")
        self.sm.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(wid, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        inp = self._card(self.sm, "INPUT")
        self.url_var = tk.StringVar(); self.cat_var = tk.StringVar(value="General"); self.limit_var = tk.IntVar(value=10)
        self._row(inp, "YouTube URL", lambda f: self._entry(f, self.url_var, 55))
        self._row(inp, "Category folder", lambda f: self._entry(f, self.cat_var, 32))
        self._row(inp, "Video limit", lambda f: self._spin(f))

        opt = self._card(self.sm, "OPTIONS"); of = tk.Frame(opt, bg=PANEL); of.pack(fill="x")
        self.ao_var = tk.BooleanVar(value=True)
        tk.Checkbutton(of, text="Auto-open folder", variable=self.ao_var, bg=PANEL, fg=TEXT, selectcolor=BG, font=("Helvetica",10), cursor="hand2").pack(side="left")

        ext = self._card(self.sm, "EXTRA AI INSTRUCTIONS")
        self.extra = tk.Text(ext, height=2, font=("Courier",9), bg=BG, fg=MUTED, insertbackground=TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER)
        self.extra.pack(fill="x"); self._ph = "e.g. Focus on business models."; self.extra.insert("1.0", self._ph)
        self.extra.bind("<FocusIn>", lambda e: self._cph()); self.extra.bind("<FocusOut>", lambda e: self._rph())

        bc = self._card(self.sm, "RUN")
        self.btn_run = tk.Button(bc, text="RUN PIPELINE", font=("Helvetica",11,"bold"), bg=ACCENT, fg=BG, relief="flat", cursor="hand2", pady=10, command=self._start)
        self.btn_run.pack(fill="x", pady=(0,8))
        br = tk.Frame(bc, bg=PANEL); br.pack(fill="x")
        self.btn_exp = self._sb(br, "Export CSV", DIM, self._ecsv)
        self.btn_rst = self._sb(br, "Reset DB", DANGER, self._rdb)
        self._btns = [self.btn_run, self.btn_exp, self.btn_rst]

        pc = self._card(self.sm, "PROGRESS"); self.pvar = tk.DoubleVar()
        ttk.Progressbar(pc, variable=self.pvar, maximum=100, style="D.Horizontal.TProgressbar").pack(fill="x", pady=(0,4))
        self.plbl = tk.Label(pc, text="Ready", bg=PANEL, fg=MUTED, font=("Helvetica",8), anchor="w"); self.plbl.pack(fill="x")

        lf = tk.Frame(self.sm, bg=BG); lf.pack(fill="both", expand=True, padx=16, pady=(8,16))
        tk.Label(lf, text="LOG", font=("Helvetica",8,"bold"), bg=BG, fg=MUTED).pack(anchor="w", pady=(0,3))
        self.log = scrolledtext.ScrolledText(lf, height=10, font=("Courier",9), bg="#050505", fg="#707070", relief="flat", wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("ok", foreground="#999"); self.log.tag_config("err", foreground=DANGER); self.log.tag_config("info", foreground="#888")

    def _build_bulk_tab(self):
        tab = tk.Frame(self.notebook, bg=BG); self.notebook.add(tab, text="  Bulk Import  ")
        hdr = tk.Frame(tab, bg=CARD, height=44); hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="BULK URL IMPORT", font=("Helvetica",11,"bold"), bg=CARD, fg=ACCENT, padx=16).pack(side="left", pady=10)
        self.blbl = tk.Label(hdr, text="", font=("Helvetica",9), bg=CARD, fg=MUTED); self.blbl.pack(side="right", padx=16)

        inst = tk.Frame(tab, bg=PANEL); inst.pack(fill="x", padx=16, pady=(8,0))
        tk.Label(inst, text="Paste URLs one per line.", bg=PANEL, fg=MUTED, font=("Helvetica",9), padx=10, pady=8).pack(anchor="w")

        uf = tk.Frame(tab, bg=BG); uf.pack(fill="both", expand=True, padx=16, pady=(8,0))
        tk.Label(uf, text="URLS", font=("Helvetica",8,"bold"), bg=BG, fg=MUTED).pack(anchor="w", pady=(0,3))
        self.btxt = scrolledtext.ScrolledText(uf, height=14, font=("Courier",9), bg="#050505", fg=TEXT, relief="flat", wrap="none", insertbackground=TEXT)
        self.btxt.pack(fill="both", expand=True)

        opt = tk.Frame(tab, bg=PANEL); opt.pack(fill="x", padx=16, pady=(8,0))
        oi = tk.Frame(opt, bg=PANEL); oi.pack(fill="x", padx=10, pady=8)
        tk.Label(oi, text="Category folder", bg=PANEL, fg=MUTED, font=("Helvetica",9)).pack(side="left")
        self.bcat_var = tk.StringVar(value="General")
        tk.Entry(oi, textvariable=self.bcat_var, bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Helvetica",10), width=20, highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=(8,16))
        self.bskip = tk.BooleanVar(value=False)
        tk.Checkbutton(oi, text="Skip shorts", variable=self.bskip, bg=PANEL, fg=TEXT, selectcolor=BG, font=("Helvetica",10), cursor="hand2").pack(side="left", padx=(0,16))
        self.bao = tk.BooleanVar(value=True)
        tk.Checkbutton(oi, text="Auto-open", variable=self.bao, bg=PANEL, fg=TEXT, selectcolor=BG, font=("Helvetica",10), cursor="hand2").pack(side="left")

        bf = tk.Frame(tab, bg=BG); bf.pack(fill="x", padx=16, pady=(8,0))
        self.bbtn = tk.Button(bf, text="PROCESS ALL URLS", font=("Helvetica",11,"bold"), bg=ACCENT, fg=BG, relief="flat", cursor="hand2", pady=10, command=self._bstart)
        self.bbtn.pack(fill="x", pady=(0,6))
        bbr = tk.Frame(bf, bg=BG); bbr.pack(fill="x")
        self._sb(bbr, "Count", DIM, self._bcount); self._sb(bbr, "Dedup", DIM, self._bdedup)
        self._sb(bbr, "Load file", DIM, self._bload); self._sb(bbr, "Clear", DANGER, lambda: self.btxt.delete("1.0","end"))

        pf = tk.Frame(tab, bg=PANEL); pf.pack(fill="x", padx=16, pady=(8,0))
        pi = tk.Frame(pf, bg=PANEL); pi.pack(fill="x", padx=10, pady=8)
        self.bpvar = tk.DoubleVar()
        ttk.Progressbar(pi, variable=self.bpvar, maximum=100, style="D.Horizontal.TProgressbar").pack(fill="x", pady=(0,4))
        self.bplbl = tk.Label(pi, text="Ready", bg=PANEL, fg=MUTED, font=("Helvetica",8), anchor="w"); self.bplbl.pack(fill="x")

        blf = tk.Frame(tab, bg=BG); blf.pack(fill="both", expand=False, padx=16, pady=(8,16))
        tk.Label(blf, text="LOG", font=("Helvetica",8,"bold"), bg=BG, fg=MUTED).pack(anchor="w", pady=(0,3))
        self.blog = scrolledtext.ScrolledText(blf, height=7, font=("Courier",9), bg="#050505", fg="#707070", relief="flat", wrap="word")
        self.blog.pack(fill="both", expand=True)
        self.blog.tag_config("ok",foreground="#999"); self.blog.tag_config("err",foreground=DANGER); self.blog.tag_config("info",foreground="#888")

    def _build_search_tab(self):
        tab = tk.Frame(self.notebook, bg=BG); self.notebook.add(tab, text="  Search  ")
        top = tk.Frame(tab, bg=PANEL); top.pack(fill="x", padx=16, pady=12)
        tk.Label(top, text="Search", bg=PANEL, fg=MUTED, font=("Helvetica",9)).pack(side="left", padx=(10,5))
        self.sv = tk.StringVar()
        tk.Entry(top, textvariable=self.sv, bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Helvetica",10), width=22, highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=5)
        tk.Label(top, text="Cat", bg=PANEL, fg=MUTED, font=("Helvetica",9)).pack(side="left", padx=(10,5))
        self.fcv = tk.StringVar(value="All"); self.fc = ttk.Combobox(top, textvariable=self.fcv, width=12, state="readonly"); self.fc["values"]=["All"]; self.fc.pack(side="left", padx=5)
        tk.Label(top, text="Score", bg=PANEL, fg=MUTED, font=("Helvetica",9)).pack(side="left", padx=(10,5))
        self.fsv = tk.IntVar(value=0); tk.Spinbox(top, from_=0, to=10, textvariable=self.fsv, width=3, font=("Helvetica",10), bg=BG, fg=TEXT, relief="flat").pack(side="left", padx=5)
        tk.Label(top, text="Ch", bg=PANEL, fg=MUTED, font=("Helvetica",9)).pack(side="left", padx=(10,5))
        self.fchv = tk.StringVar(value="All"); self.fch = ttk.Combobox(top, textvariable=self.fchv, width=14, state="readonly"); self.fch["values"]=["All"]; self.fch.pack(side="left", padx=5)
        tk.Button(top, text="GO", font=("Helvetica",9,"bold"), bg=ACCENT, fg=BG, relief="flat", padx=12, command=self._search).pack(side="left", padx=(10,5))
        tk.Button(top, text="Refresh", font=("Helvetica",8), bg=DIM, fg=TEXT, relief="flat", padx=8, command=self._rfilt).pack(side="left", padx=5)
        cols = ("title","channel","category","score","difficulty","technologies","tags","topics")
        self.st = ttk.Treeview(tab, columns=cols, show="headings", height=25)
        for c2, w2 in zip(cols, [240,110,80,50,80,150,130,130]): self.st.heading(c2, text=c2.upper()); self.st.column(c2, width=w2, minwidth=40)
        self.st.pack(fill="both", expand=True, padx=16, pady=(0,8))
        self.scnt = tk.Label(tab, text="", bg=BG, fg=MUTED, font=("Helvetica",8)); self.scnt.pack(anchor="w", padx=16, pady=(0,8))

    def _build_channels_tab(self):
        tab = tk.Frame(self.notebook, bg=BG); self.notebook.add(tab, text="  Channels  ")
        top = tk.Frame(tab, bg=PANEL); top.pack(fill="x", padx=16, pady=12)
        tk.Label(top, text="CHANNEL COMPARISON", bg=PANEL, fg=MUTED, font=("Helvetica",9,"bold")).pack(side="left", padx=10)
        tk.Button(top, text="Refresh", font=("Helvetica",9), bg=DIM, fg=TEXT, relief="flat", padx=12, command=self._rchan).pack(side="right", padx=10)
        cols = ("channel","videos","avg_quality","top_category","top_tech","first_seen","last_seen")
        self.ct = ttk.Treeview(tab, columns=cols, show="headings", height=20)
        for c2, w2 in zip(cols, [180,60,80,110,220,90,90]): self.ct.heading(c2, text=c2.replace("_"," ").upper()); self.ct.column(c2, width=w2, minwidth=40)
        self.ct.pack(fill="both", expand=True, padx=16, pady=(0,16))

    def _build_insights_tab(self):
        tab = tk.Frame(self.notebook, bg=BG); self.notebook.add(tab, text="  Insights  ")
        top = tk.Frame(tab, bg=PANEL); top.pack(fill="x", padx=16, pady=12)
        tk.Label(top, text="AI RECOMMENDATIONS", bg=PANEL, fg=MUTED, font=("Helvetica",9,"bold")).pack(side="left", padx=10)
        tk.Button(top, text="Generate", font=("Helvetica",9,"bold"), bg=ACCENT, fg=BG, relief="flat", padx=14, command=self._insights).pack(side="right", padx=10)
        sf = tk.Frame(tab, bg=BG); sf.pack(fill="x", padx=16, pady=(0,8)); self.slbls = {}
        sff = tk.Frame(sf, bg=BG); sff.pack(fill="x")
        for l in ["Videos","Avg Score","Channels","History"]:
            box = tk.Frame(sff, bg=CARD, padx=20, pady=10); box.pack(side="left", padx=(0,6), fill="x", expand=True)
            vl = tk.Label(box, text="0", bg=CARD, fg=ACCENT, font=("Helvetica",18,"bold")); vl.pack()
            tk.Label(box, text=l.upper(), bg=CARD, fg=MUTED, font=("Helvetica",8)).pack(); self.slbls[l] = vl
        self.ibox = scrolledtext.ScrolledText(tab, height=18, font=("Courier",9), bg="#050505", fg="#808080", relief="flat", wrap="word")
        self.ibox.pack(fill="both", expand=True, padx=16, pady=(8,8))
        self.tf = tk.Frame(tab, bg=PANEL); self.tf.pack(fill="x", padx=16, pady=(0,16))
        tk.Label(self.tf, text="TOP TECH", bg=PANEL, fg=MUTED, font=("Helvetica",8,"bold")).pack(anchor="w", padx=10, pady=(8,4))
        self.tlbl = tk.Label(self.tf, text="No data", bg=PANEL, fg=DIM, font=("Helvetica",9), anchor="w", justify="left"); self.tlbl.pack(fill="x", padx=10, pady=(0,8))

    def _build_export_tab(self):
        tab = tk.Frame(self.notebook, bg=BG); self.notebook.add(tab, text="  Export  ")
        hdr = tk.Frame(tab, bg=CARD, height=44); hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="EXPORT KNOWLEDGE BASE", font=("Helvetica",11,"bold"), bg=CARD, fg=ACCENT, padx=16).pack(side="left", pady=10)
        # Regenerate Dashboard — prominent at the top
        dc = tk.Frame(tab, bg=PANEL); dc.pack(fill="x", padx=16, pady=(8,0))
        di = tk.Frame(dc, bg=PANEL); di.pack(fill="x", padx=14, pady=10)
        tk.Label(di, text="Regenerate Dashboard", bg=PANEL, fg=TEXT, font=("Helvetica",10,"bold")).pack(anchor="w")
        tk.Label(di, text="Rebuild HTML + MD dashboard from current database. Opens in browser.", bg=PANEL, fg=MUTED, font=("Helvetica",9)).pack(anchor="w", pady=(2,6))
        tk.Button(di, text="REGENERATE DASHBOARD", font=("Helvetica",10,"bold"), bg=ACCENT, fg=BG, relief="flat", padx=16, pady=6, cursor="hand2", command=self._regen_dash).pack(anchor="w")

        for t, s, cmd in [
            ("JSON Knowledge Base", "Videos + chunks + facts + entity index. Best for Claude API.", self._ejson),
            ("Claude Markdown", "Structured MD with entity index and facts. Best for Claude chat.", self._emd),
            ("KB Summary", "Table of contents. Give to AI first.", self._esum),
            ("CSV", "Flat spreadsheet.", self._ecsv)]:
            c2 = tk.Frame(tab, bg=PANEL); c2.pack(fill="x", padx=16, pady=(8,0))
            ci = tk.Frame(c2, bg=PANEL); ci.pack(fill="x", padx=14, pady=10)
            tk.Label(ci, text=t, bg=PANEL, fg=TEXT, font=("Helvetica",10,"bold")).pack(anchor="w")
            tk.Label(ci, text=s, bg=PANEL, fg=MUTED, font=("Helvetica",9)).pack(anchor="w", pady=(2,6))
            tk.Button(ci, text=f"Export", font=("Helvetica",9), bg=DIM, fg=TEXT, relief="flat", padx=14, pady=4, cursor="hand2", command=cmd).pack(anchor="w")

    # ── Helpers ──
    def _card(self, p, t):
        o = tk.Frame(p, bg=BG); o.pack(fill="x", padx=16, pady=(6,0))
        tk.Label(o, text=t, font=("Helvetica",8,"bold"), bg=BG, fg=MUTED).pack(anchor="w", pady=(0,3))
        c2 = tk.Frame(o, bg=PANEL); c2.pack(fill="x"); i = tk.Frame(c2, bg=PANEL); i.pack(fill="x", padx=14, pady=10); return i
    def _row(self, p, l, fn):
        f = tk.Frame(p, bg=PANEL); f.pack(fill="x", pady=3)
        tk.Label(f, text=l, width=18, anchor="w", bg=PANEL, fg=MUTED, font=("Helvetica",9)).pack(side="left"); fn(f)
    def _entry(self, p, v, w=52):
        tk.Entry(p, textvariable=v, bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Helvetica",10), width=w, highlightthickness=1, highlightbackground=BORDER, highlightcolor=DIM).pack(side="left", padx=(0,8))
    def _spin(self, p):
        tk.Spinbox(p, from_=1, to=500, textvariable=self.limit_var, width=5, font=("Helvetica",10), bg=BG, fg=TEXT, buttonbackground=PANEL, relief="flat", highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=(0,8))
        tk.Label(p, text="auto 1 for single video", bg=PANEL, fg=DIM, font=("Helvetica",8)).pack(side="left")
    def _sb(self, p, t, bg, cmd):
        b = tk.Button(p, text=t, font=("Helvetica",8), bg=bg, fg=TEXT, relief="flat", cursor="hand2", padx=10, pady=5, activebackground=HOVER, command=cmd); b.pack(side="left", padx=(0,6)); return b
    def _cph(self):
        if self.extra.get("1.0","end-1c") == self._ph: self.extra.delete("1.0","end"); self.extra.config(fg=TEXT)
    def _rph(self):
        if not self.extra.get("1.0","end-1c").strip(): self.extra.insert("1.0", self._ph); self.extra.config(fg=MUTED)

    def _wlog(self, box, msg):
        tag = "err" if "[ERROR]" in msg else ("info" if "Claude" in msg or "Apify" in msg else ("ok" if any(w in msg.lower() for w in ("done","saved","updated","dashboard","skip")) else None))
        box.insert("end", msg+"\n", tag or ""); box.see("end"); self.update_idletasks()
    def _mlog(self, m): self._wlog(self.log, m)
    def _blg(self, m): self._wlog(self.blog, m)

    def _sp(self, done, total):
        pct = done/total*100 if total else 0; self.pvar.set(pct); self.plbl.config(text=f"{done}/{total} ({pct:.0f}%)"); self.update_idletasks()
    def _bsp(self, done, total):
        pct = done/total*100 if total else 0; self.bpvar.set(pct); self.bplbl.config(text=f"{done}/{total} ({pct:.0f}%)"); self.update_idletasks()
    def _refresh_db(self):
        self.db_lbl.config(text=f"{db_get_video_count()} videos | {history_count()} history")

    # ── Actions ──
    def _start(self):
        url = self.url_var.get().strip()
        if not url: messagebox.showwarning("Missing URL","Enter a YouTube URL"); return
        cat = self.cat_var.get().strip() or "General"
        ext = self.extra.get("1.0","end").strip()
        if ext == self._ph: ext = ""
        self.log.delete("1.0","end"); self._mlog(f"Pipeline | URL: {url} | Category: {cat}"); self._mlog("")
        self._sp(0,1)
        for b in self._btns: b.config(state="disabled")
        def done():
            for b in self._btns: b.config(state="normal")
            self.plbl.config(text="Done"); self._refresh_db(); self._rfilt()
        threading.Thread(target=run_pipeline, kwargs=dict(url=url, category=cat, max_videos=self.limit_var.get(),
            auto_open=self.ao_var.get(), extra=ext, log=self._mlog, set_progress=self._sp, on_done=done), daemon=True).start()

    def _purls(self):
        return [l.strip() for l in self.btxt.get("1.0","end").splitlines() if l.strip() and l.strip().lower()!="url" and ("youtube.com/" in l or "youtu.be/" in l)]

    def _bcount(self):
        urls = self._purls(); shorts = sum(1 for u in urls if "/shorts/" in u)
        ah = history_get_all(); already = sum(1 for u in urls if extract_video_id(u) in ah)
        self.blbl.config(text=f"{len(urls)} URLs ({len(urls)-shorts} vids, {shorts} shorts, {already} done)")

    def _bdedup(self):
        urls = self._purls(); seen = set(); dd = []
        for u in urls:
            v = extract_video_id(u)
            if v not in seen: seen.add(v); dd.append(u)
        self.btxt.delete("1.0","end"); self.btxt.insert("1.0","\n".join(dd))
        self.blbl.config(text=f"{len(dd)} unique ({len(urls)-len(dd)} dupes)")

    def _bload(self):
        p = filedialog.askopenfilename(filetypes=[("Text/CSV","*.txt *.csv"),("All","*.*")])
        if p:
            with open(p,"r",encoding="utf-8") as f: self.btxt.delete("1.0","end"); self.btxt.insert("1.0",f.read())
            self._bcount()

    def _bstart(self):
        urls = self._purls()
        if not urls: messagebox.showwarning("No URLs","Paste URLs"); return
        if self.bskip.get():
            b4 = len(urls); urls = [u for u in urls if "/shorts/" not in u]; self._blg(f"Skipped {b4-len(urls)} shorts")
        ah = history_get_all(); new = [u for u in urls if extract_video_id(u) not in ah]
        if not new: messagebox.showinfo("Done","All in history"); return
        cat = self.bcat_var.get().strip() or "General"
        self.blog.delete("1.0","end"); self._blg(f"Bulk: {len(new)} new | Category: {cat}"); self._blg("")
        self.bbtn.config(state="disabled"); self._bsp(0, len(new))
        def done():
            self.bbtn.config(state="normal"); self.bplbl.config(text="Done"); self._refresh_db(); self._rfilt()
        threading.Thread(target=run_bulk_pipeline, kwargs=dict(urls=new, category=cat, auto_open=self.bao.get(),
            log=self._blg, set_progress=self._bsp, on_done=done), daemon=True).start()

    def _regen_dash(self):
        cnt = db_get_video_count()
        if cnt == 0:
            messagebox.showwarning("No Data", "No videos in database. Run a pipeline first.")
            return
        out = get_all_dir()
        generate_dashboard(out, ALL_FOLDER, lambda m: None)
        # Also regenerate per-channel dashboards
        conn = sqlite3.connect(DB_FILE); c = conn.cursor()
        c.execute("SELECT DISTINCT channel, project_category FROM videos WHERE channel != ''")
        channels = c.fetchall(); conn.close()
        for ch, cat in channels:
            ch_dir = get_channel_dir(cat or "General", ch)
            generate_dashboard(ch_dir, ch, lambda m: None)
        messagebox.showinfo("Dashboard", f"Dashboard regenerated!\n{cnt} videos\nFolder: {out}")
        # Auto-open
        try:
            html_path = os.path.join(out, f"{safe_name(ALL_FOLDER)}_Dashboard.html")
            if os.path.exists(html_path):
                import webbrowser
                webbrowser.open(html_path)
            else:
                if sys.platform == "win32": os.startfile(out)
                elif sys.platform == "darwin": subprocess.run(["open", out])
                else: subprocess.run(["xdg-open", out])
        except: pass

    def _ecsv(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")], initialfile="export.csv")
        if p: n = export_to_csv(p); messagebox.showinfo("Export", f"{n} videos")
    def _ejson(self):
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON","*.json")], initialfile="knowledge_base.json")
        if p: n = export_knowledge_base_json(p); messagebox.showinfo("Export", f"{n} videos")
    def _emd(self):
        p = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("MD","*.md")], initialfile="knowledge_base.md")
        if p: n = export_claude_markdown(p); messagebox.showinfo("Export", f"{n} videos")
    def _esum(self):
        p = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("MD","*.md")], initialfile="kb_summary.md")
        if p: n = export_kb_summary(p); messagebox.showinfo("Export", f"{n} videos")
    def _rdb(self):
        if messagebox.askyesno("Reset","Delete ALL data including history?"):
            conn = sqlite3.connect(DB_FILE)
            for t in ("videos","channels","chunks","facts","entity_index","history"): conn.execute(f"DELETE FROM {t}")
            conn.commit(); conn.close(); self._mlog("Database + history cleared"); self._refresh_db()

    def _search(self):
        for i in self.st.get_children(): self.st.delete(i)
        r = db_search(self.sv.get().strip(), self.fcv.get(), self.fsv.get(), self.fchv.get())
        for x in r: self.st.insert("","end", values=(x[1],x[3],x[4],x[5],x[6],x[7],x[8],x[9]))
        self.scnt.config(text=f"{len(r)} results")

    def _rfilt(self):
        self.fc["values"] = ["All"] + db_get_all_categories()
        self.fch["values"] = ["All"] + db_get_all_channels()

    def _rchan(self):
        for i in self.ct.get_children(): self.ct.delete(i)
        for r in db_get_channel_comparison(): self.ct.insert("","end", values=r)

    def _insights(self):
        stats = db_get_stats()
        self.slbls["Videos"].config(text=str(stats["total"]))
        self.slbls["Avg Score"].config(text=f"{stats['avg_score']}/10")
        self.slbls["Channels"].config(text=str(stats["channels"]))
        self.slbls["History"].config(text=str(history_count()))
        self.tlbl.config(text="\n".join(f"  {i+1}. {t} ({c}x)" for i,(t,c) in enumerate(stats["top_tech"])) or "No data")
        if stats["total"]==0: self.ibox.delete("1.0","end"); self.ibox.insert("1.0","No data"); return
        conn = sqlite3.connect(DB_FILE); c = conn.cursor()
        c.execute("SELECT title, channel, category, quality_score, technologies FROM videos ORDER BY quality_score DESC LIMIT 15")
        top = c.fetchall(); conn.close()
        s = f"Videos:{stats['total']} AvgQ:{stats['avg_score']} Ch:{stats['channels']}\nTech:{','.join(t for t,_ in stats['top_tech'][:5])}\n"
        for t,ch,cat,sc,tech in top[:10]: s += f"- {t[:40]}|{ch}|{cat}|{sc}|{tech}\n"
        self.ibox.delete("1.0","end"); self.ibox.insert("1.0","Generating..."); self.update_idletasks()
        def run():
            r = call_claude(RECOMMEND_SYSTEM, build_recommend_prompt(s), lambda m:None, max_tokens=500)
            self.ibox.delete("1.0","end"); self.ibox.insert("1.0", r if r else "Failed")
        threading.Thread(target=run, daemon=True).start()


if __name__ == "__main__":
    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)
    init_db()
    App().mainloop()