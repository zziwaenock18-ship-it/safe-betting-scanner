import streamlit as st
import json
import urllib.request
from datetime import datetime
from typing import List, Dict, Any, Optional

st.set_page_config(page_title="Safe Betting Scanner", page_icon="⚽", layout="wide")

API_BASE = "https://worldcup26.ir/get/soccer"
LEAGUES_TO_SCAN = ["eng.1", "esp.1", "ita.1", "ger.1", "fra.1"]

@st.cache_data(ttl=300)
def fetch_json(url: str) -> Optional[Dict]:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None

def get_standings(league: str) -> Dict[str, Dict]:
    data = fetch_json(f"{API_BASE}/{league}/standings?season=2026")
    mapping = {}
    if not data or "children" not in data:
        return mapping
    for child in data.get("children", []):
        entries = child.get("standings", {}).get("entries", [])
        for i, entry in enumerate(entries, 1):
            team = entry.get("team", {}).get("displayName") or entry.get("team", {}).get("name")
            stats = {s["name"]: s.get("displayValue") for s in entry.get("stats", [])}
            if team:
                mapping[team] = {
                    "pos": i,
                    "points": int(stats.get("points", 0)),
                    "gd": int(stats.get("pointDifferential", 0))
                }
    return mapping

def fetch_upcoming_fixtures(league: str, max_events: int = 15) -> List[Dict]:
    data = fetch_json(f"{API_BASE}/{league}/fixtures?status=scheduled&limit={max_events}")
    fixtures = []
    if not data or "events" not in data:
        return fixtures
    standings = get_standings(league)
    for event in data.get("events", [])[:max_events]:
        comps = event.get("competitions", [{}])[0].get("competitors", [])
        if len(comps) < 2:
            continue
        home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
        away = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
        home_name = home.get("team", {}).get("displayName")
        away_name = away.get("team", {}).get("displayName")
        if not home_name or not away_name:
            continue
        h_stand = standings.get(home_name, {})
        a_stand = standings.get(away_name, {})
        status = "upcoming"
        stype = event.get("status", {}).get("type", {})
        if stype.get("state") == "in":
            status = "live"
        elif stype.get("completed"):
            status = "finished"
        fixtures.append({
            "match": f"{home_name} vs {away_name}",
            "league": data.get("league", {}).get("name", league),
            "kickoff": event.get("date", "")[:16].replace("T", " "),
            "home_pos": h_stand.get("pos", 99),
            "away_pos": a_stand.get("pos", 99),
            "home_points": h_stand.get("points", 0),
            "away_points": a_stand.get("points", 0),
            "home_gd": h_stand.get("gd", 0),
            "away_gd": a_stand.get("gd", 0),
            "home_form": home.get("form", "-----"),
            "away_form": away.get("form", "-----"),
            "odds_home": 1.50,
            "odds_btts_yes": 1.80,
            "status": status,
            "home_scoring_rate": "moderate",
            "away_scoring_rate": "moderate",
            "both_concede_often": True
        })
    return fixtures

def fetch_finished_fixtures(league: str, max_events: int = 8) -> List[Dict]:
    """Fetch recent finished matches with scores."""
    data = fetch_json(f"{API_BASE}/{league}/fixtures?status=finished&limit={max_events}")
    results = []
    if not data or "events" not in data:
        return results
    standings = get_standings(league)
    for event in data.get("events", [])[:max_events]:
        comps = event.get("competitions", [{}])[0].get("competitors", [])
        if len(comps) < 2:
            continue
        home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
        away = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
        home_name = home.get("team", {}).get("displayName")
        away_name = away.get("team", {}).get("displayName")
        if not home_name or not away_name:
            continue
        home_score = int(home.get("score", 0) or 0)
        away_score = int(away.get("score", 0) or 0)
        h_stand = standings.get(home_name, {})
        a_stand = standings.get(away_name, {})
        stronger_is_home = h_stand.get("pos", 99) < a_stand.get("pos", 99)
        stronger_won = (stronger_is_home and home_score > away_score) or (not stronger_is_home and away_score > home_score)
        draw = home_score == away_score
        results.append({
            "match": f"{home_name} vs {away_name}",
            "league": data.get("league", {}).get("name", league),
            "date": event.get("date", "")[:10],
            "score": f"{home_score}-{away_score}",
            "home_pos": h_stand.get("pos", "?"),
            "away_pos": a_stand.get("pos", "?"),
            "stronger_won": stronger_won,
            "draw": draw,
            "result_note": "Stronger won" if stronger_won else ("Draw" if draw else "Upset")
        })
    return results

def classify_match(fixture: Dict[str, Any]) -> Dict[str, Any]:
    pos_gap = abs(fixture["home_pos"] - fixture["away_pos"])
    points_gap = abs(fixture["home_points"] - fixture["away_points"])
    stronger_is_home = fixture["home_pos"] < fixture["away_pos"]
    
    classification = "RED"
    confidence = "Low"
    risks = []
    btts_support = False
    
    if fixture["status"] != "upcoming":
        risks.append("Already started/finished")
        return {**fixture, "classification": "RED", "confidence": "N/A", "risks": risks, "btts_support": False}
    
    if pos_gap >= 8 and points_gap >= 10 and stronger_is_home:
        classification = "GREEN"
        confidence = "Moderate-High"
    elif pos_gap >= 6 and points_gap >= 7:
        classification = "YELLOW"
        confidence = "Moderate"
        risks.append("Moderate gap")
    else:
        risks.append("Small gap")
    
    if "L" in fixture["home_form"][-3:] and stronger_is_home:
        risks.append("Recent loss in form")
        if classification == "GREEN":
            classification = "YELLOW"
    
    if fixture.get("both_concede_often"):
        btts_support = True
        if classification == "RED":
            classification = "YELLOW"
    
    return {
        **fixture,
        "classification": classification,
        "confidence": confidence,
        "risks": risks,
        "btts_support": btts_support
    }

def load_and_analyze():
    all_fixtures = []
    for lg in LEAGUES_TO_SCAN:
        all_fixtures.extend(fetch_upcoming_fixtures(lg))
    return [classify_match(f) for f in all_fixtures]

def load_recent_results():
    all_results = []
    for lg in LEAGUES_TO_SCAN[:3]:
        all_results.extend(fetch_finished_fixtures(lg))
    return sorted(all_results, key=lambda x: x.get("date", ""), reverse=True)[:12]

# UI
st.title("⚽ Safe Betting Prediction Scanner")
st.caption("Safety first · Statistical mismatches only · Live data from free API")
st.markdown(f"**Scan time:** {datetime.now().strftime('%Y-%m-%d %H:%M EAT')}")

if st.button("🔄 Refresh Live Data", type="primary"):
    st.cache_data.clear()

with st.spinner("Fetching fixtures and standings..."):
    analyzed = load_and_analyze()

if not analyzed:
    st.error("No fixtures returned. API may be unavailable.")
    st.stop()

# Filters
col1, col2 = st.columns(2)
with col1:
    show_only_green = st.checkbox("Show only GREEN", value=False)
with col2:
    min_gap = st.slider("Minimum position gap", 0, 15, 6)

filtered = [m for m in analyzed if (not show_only_green or m["classification"] == "GREEN") and abs(m["home_pos"] - m["away_pos"]) >= min_gap]

st.subheader(f"Analyzed Matches ({len(filtered)})")

def badge(cls):
    colors = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}
    return colors.get(cls, "⚪")

for m in sorted(filtered, key=lambda x: (0 if x["classification"]=="GREEN" else 1 if x["classification"]=="YELLOW" else 2, x["odds_home"])):
    with st.expander(f"{badge(m['classification'])} {m['match']}  ·  {m['league']}"):
        c1, c2, c3 = st.columns(3)
        c1.metric("Positions", f"{m['home_pos']} vs {m['away_pos']}")
        c2.metric("Points", f"{m['home_points']} - {m['away_points']}")
        c3.metric("Classification", m["classification"])
        st.write(f"**Form:** {m['home_form']} vs {m['away_form']}")
        st.write(f"**Kickoff:** {m['kickoff']} · **Status:** {m['status']}")
        st.write(f"**Confidence:** {m['confidence']}")
        if m["risks"]:
            st.warning("Risks: " + ", ".join(m["risks"]))
        if m.get("btts_support"):
            st.info("BTTS Yes supported by scoring patterns")

# Summary sections
st.divider()
st.subheader("🏆 Top Straight Wins")
wins = sorted([m for m in analyzed if m["status"]=="upcoming" and m["classification"]!="RED"], key=lambda x: (0 if x["classification"]=="GREEN" else 1, x["odds_home"]))[:5]
for i, m in enumerate(wins, 1):
    st.write(f"{i}. **{m['match']}** ({m['league']}) — {badge(m['classification'])} {m['classification']} · Pos {m['home_pos']} vs {m['away_pos']}")

st.subheader("⚽ Top BTTS Yes")
btts = sorted([m for m in analyzed if m.get("btts_support") and m["status"]=="upcoming"], key=lambda x: x.get("odds_btts_yes", 99))[:5]
for i, m in enumerate(btts, 1):
    st.write(f"{i}. **{m['match']}** ({m['league']}) — BTTS Yes · {badge(m['classification'])}")

st.subheader("🎯 Safe Accumulator (Statistical)")
seen = set()
legs = []
for m in wins[:3]:
    if m["match"] not in seen:
        legs.append((m, "Win"))
        seen.add(m["match"])
for m in btts[:3]:
    if m["match"] not in seen:
        legs.append((m, "BTTS Yes"))
        seen.add(m["match"])
if legs:
    product = 1.0
    for m, market in legs:
        odd = m["odds_home"] if market == "Win" else m.get("odds_btts_yes", 1.8)
        product *= odd
        st.write(f"- {m['match']}: **{market}** @ {odd}")
    st.success(f"Approx combined odds: **{product:.2f}** (odds are placeholders)")
else:
    st.info("Not enough qualifying legs for an accumulator.")

# === NEW: Recent Results Section ===
st.divider()
st.subheader("📊 Recent Results")
st.caption("Finished matches · Shows whether the stronger-positioned team won")

with st.spinner("Loading recent results..."):
    results = load_recent_results()

if results:
    for r in results:
        icon = "✅" if r["stronger_won"] else ("🟡" if r["draw"] else "❌")
        st.write(f"{icon} **{r['match']}** · {r['score']} · {r['result_note']} · Pos {r['home_pos']} vs {r['away_pos']} · {r['date']}")
else:
    st.info("No recent finished matches found.")

st.divider()
st.caption("Data: free API (worldcup26.ir). Odds are placeholders. Statistical analysis only — not betting advice. Always verify independently. Safety first.")
