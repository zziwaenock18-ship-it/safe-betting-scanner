import streamlit as st
import json
import urllib.request
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import random

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

def estimate_safety(pos_gap: int, points_gap: int, form_score: int) -> int:
    """Estimate safety % from available stats. Higher gap + better form = higher %."""
    base = 55
    base += min(pos_gap * 2.5, 25)
    base += min(points_gap * 0.8, 12)
    base += form_score * 2
    return min(int(base), 92)

def form_score(form: str) -> int:
    if not form or form == "-----":
        return 0
    return form.count("W") - form.count("L")

def classify_match(fixture: Dict[str, Any]) -> Dict[str, Any]:
    pos_gap = abs(fixture["home_pos"] - fixture["away_pos"])
    points_gap = abs(fixture["home_points"] - fixture["away_points"])
    stronger_is_home = fixture["home_pos"] < fixture["away_pos"]
    h_form = form_score(fixture.get("home_form", ""))
    a_form = form_score(fixture.get("away_form", ""))
    
    classification = "RED"
    confidence = "Low"
    risks = []
    btts_support = False
    markets = []
    
    if fixture["status"] != "upcoming":
        risks.append("Already started/finished")
        return {**fixture, "classification": "RED", "confidence": "N/A", "risks": risks, "btts_support": False, "markets": [], "safety_pct": 0}
    
    safety = estimate_safety(pos_gap, points_gap, h_form if stronger_is_home else a_form)
    
    if pos_gap >= 8 and points_gap >= 10 and stronger_is_home:
        classification = "GREEN"
        confidence = "Moderate-High"
        markets.append(("Straight Win", 1.45, safety))
        markets.append(("Double Chance", 1.15, min(safety + 8, 95)))
    elif pos_gap >= 6 and points_gap >= 7:
        classification = "YELLOW"
        confidence = "Moderate"
        risks.append("Moderate gap")
        markets.append(("Double Chance", 1.28, safety))
        markets.append(("Straight Win", 1.65, max(safety - 10, 50)))
    else:
        risks.append("Small gap")
        markets.append(("Double Chance", 1.45, max(safety - 15, 45)))
    
    if "L" in fixture["home_form"][-3:] and stronger_is_home:
        risks.append("Recent loss in form")
        if classification == "GREEN":
            classification = "YELLOW"
        safety = max(safety - 8, 50)
    
    # BTTS / Over estimates based on form and gap
    if pos_gap >= 4 and (h_form >= 0 or a_form >= 0):
        btts_support = True
        markets.append(("BTTS Yes", 1.75, max(safety - 12, 48)))
        markets.append(("Over 1.5", 1.35, min(safety + 5, 90)))
        markets.append(("Over 2.5", 1.85, max(safety - 8, 45)))
    
    return {
        **fixture,
        "classification": classification,
        "confidence": confidence,
        "risks": risks,
        "btts_support": btts_support,
        "markets": markets,
        "safety_pct": safety
    }

def fetch_upcoming_fixtures(league: str, max_events: int = 20) -> List[Dict]:
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
        kickoff_str = event.get("date", "")
        fixtures.append({
            "match": f"{home_name} vs {away_name}",
            "league": data.get("league", {}).get("name", league),
            "kickoff": kickoff_str[:16].replace("T", " ") if kickoff_str else "",
            "kickoff_hour_eat": int(kickoff_str[11:13]) + 3 if kickoff_str and len(kickoff_str) > 13 else 15,  # rough UTC+3
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

def fetch_finished_fixtures(league: str, max_events: int = 10) -> List[Dict]:
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

def load_and_analyze():
    all_fixtures = []
    for lg in LEAGUES_TO_SCAN:
        all_fixtures.extend(fetch_upcoming_fixtures(lg))
    return [classify_match(f) for f in all_fixtures]

def load_recent_results():
    all_results = []
    for lg in LEAGUES_TO_SCAN[:3]:
        all_results.extend(fetch_finished_fixtures(lg))
    return sorted(all_results, key=lambda x: x.get("date", ""), reverse=True)[:15]

def get_stats_note(match: Dict) -> str:
    """Generate a short stats note with source references."""
    notes = []
    notes.append(f"Position gap: {abs(match['home_pos']-match['away_pos'])} places")
    notes.append(f"Points gap: {abs(match['home_points']-match['away_points'])}")
    notes.append(f"Form: {match.get('home_form','?')} vs {match.get('away_form','?')}")
    notes.append(f"GD: {match.get('home_gd',0)} vs {match.get('away_gd',0)}")
    notes.append("Sources: worldcup26.ir standings + recent form (free public API)")
    return " · ".join(notes)

# ========== UI ==========
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

for m in sorted(filtered, key=lambda x: (0 if x["classification"]=="GREEN" else 1 if x["classification"]=="YELLOW" else 2, -x.get("safety_pct",0))):
    with st.expander(f"{badge(m['classification'])} {m['match']}  ·  {m['league']} · {m.get('safety_pct',0)}%"):
        c1, c2, c3 = st.columns(3)
        c1.metric("Positions", f"{m['home_pos']} vs {m['away_pos']}")
        c2.metric("Points", f"{m['home_points']} - {m['away_points']}")
        c3.metric("Safety", f"{m.get('safety_pct',0)}%")
        st.write(f"**Form:** {m['home_form']} vs {m['away_form']}")
        st.write(f"**Kickoff:** {m['kickoff']} · **Status:** {m['status']}")
        st.write(f"**Confidence:** {m['confidence']}")
        if m.get("markets"):
            st.write("**Suggested markets:** " + ", ".join([f"{mk[0]} @ {mk[1]} ({mk[2]}%)" for mk in m["markets"][:3]]))
        if m["risks"]:
            st.warning("Risks: " + ", ".join(m["risks"]))
        st.caption(get_stats_note(m))

# ========== BIG STAKER ==========
st.divider()
st.header("💰 Big Staker")
st.caption("Safest single ~1.5 odd + Weekly Accumulator from strongest statistical evidence")

# Safest ~1.5
safe_15 = []
for m in analyzed:
    if m["status"] != "upcoming":
        continue
    for mk, odd, pct in m.get("markets", []):
        if 1.35 <= odd <= 1.65 and pct >= 70:
            safe_15.append((m, mk, odd, pct))
safe_15.sort(key=lambda x: -x[3])

st.subheader("🎯 Safest ~1.5 Odd")
if safe_15:
    top = safe_15[0]
    st.success(f"**{top[0]['match']}** — {top[1]} @ {top[2]} · Safety: **{top[3]}%**")
    st.caption(get_stats_note(top[0]))
else:
    st.info("No clear ~1.5 safety pick from current data. Check Double Chance options below.")

# Weekly Accumulator (Mon-Sun conceptual)
st.subheader("📅 Weekly Accumulator (Mon–Sun)")
st.caption("Built from the safest available markets. Odds are estimated. Track results as matches finish.")

acca_legs = []
seen = set()
for m in sorted([x for x in analyzed if x["status"]=="upcoming" and x.get("safety_pct",0)>=65], key=lambda x: -x.get("safety_pct",0)):
    if m["match"] in seen:
        continue
    best_market = max(m.get("markets", []), key=lambda x: x[2], default=None)
    if best_market:
        acca_legs.append((m, best_market[0], best_market[1], best_market[2]))
        seen.add(m["match"])
    if len(acca_legs) >= 5:
        break

if acca_legs:
    product = 1.0
    total_safety = 1.0
    for m, mk, odd, pct in acca_legs:
        product *= odd
        total_safety *= (pct / 100)
        st.write(f"- **{m['match']}**: {mk} @ {odd} · **{pct}%** safety")
        st.caption(get_stats_note(m))
    st.success(f"Combined odds ≈ **{product:.2f}** · Combined safety ≈ **{total_safety*100:.1f}%**")
    st.info("As matches finish, check the Recent Results section to see which legs won/lost.")
else:
    st.info("Not enough high-safety legs for a weekly accumulator right now.")

# ========== NIGHT GAMES (00:00–05:00 EAT) ==========
st.divider()
st.header("🌙 Safest Night Games (00:00–05:00 EAT)")
st.caption("Daily · Target ~2.0 odds (single or small combo) · Only from safest statistical options")

night = [m for m in analyzed if m["status"]=="upcoming" and 0 <= m.get("kickoff_hour_eat", 15) <= 5]
night_safe = []
for m in night:
    for mk, odd, pct in m.get("markets", []):
        if pct >= 68:
            night_safe.append((m, mk, odd, pct))
night_safe.sort(key=lambda x: -x[3])

if night_safe:
    # Try single ~2.0 or combo
    singles = [x for x in night_safe if 1.85 <= x[2] <= 2.15]
    if singles:
        top = singles[0]
        st.success(f"**Single:** {top[0]['match']} — {top[1]} @ {top[2]} · **{top[3]}%**")
        st.caption(get_stats_note(top[0]))
    else:
        # Combo to ~2.0
        if len(night_safe) >= 2:
            a, b = night_safe[0], night_safe[1]
            combo_odd = a[2] * b[2]
            combo_pct = (a[3] * b[3]) / 100
            st.success(f"**Combo (~2.0):** {a[0]['match']} ({a[1]} @ {a[2]}) + {b[0]['match']} ({b[1]} @ {b[2]})")
            st.write(f"Combined odds ≈ **{combo_odd:.2f}** · Combined safety ≈ **{combo_pct:.1f}%**")
            st.caption(get_stats_note(a[0]) + " | " + get_stats_note(b[0]))
        else:
            st.info("Limited night games with high safety right now.")
else:
    st.info("No night games (00:00–05:00 EAT) with sufficient safety in the current scan. Most European fixtures kick off in the evening local time.")

# ========== 30-DAY PERFORMANCE ==========
st.divider()
st.header("📈 Past 30 Days Performance")
st.caption("Based on recent finished matches where the stronger-positioned team was tracked")

results = load_recent_results()
if results:
    wins = sum(1 for r in results if r["stronger_won"])
    draws = sum(1 for r in results if r["draw"])
    losses = len(results) - wins - draws
    win_rate = (wins / len(results) * 100) if results else 0
    st.metric("Stronger team win rate", f"{win_rate:.0f}%", f"{wins}W / {draws}D / {losses}L in last {len(results)} tracked")
    for r in results[:8]:
        icon = "✅" if r["stronger_won"] else ("🟡" if r["draw"] else "❌")
        st.write(f"{icon} **{r['match']}** · {r['score']} · {r['result_note']} · {r['date']}")
else:
    st.info("No recent finished data available for performance tracking.")

# ========== RECENT RESULTS (existing) ==========
st.divider()
st.subheader("📊 All Recent Results")
if results:
    for r in results:
        icon = "✅" if r["stronger_won"] else ("🟡" if r["draw"] else "❌")
        st.write(f"{icon} **{r['match']}** · {r['score']} · {r['result_note']} · Pos {r['home_pos']} vs {r['away_pos']} · {r['date']}")
else:
    st.info("No recent finished matches found.")

st.divider()
st.caption("Data: free API (worldcup26.ir). Odds and safety % are estimated from league position, points, GD and form — not live bookmaker prices. Statistical analysis only — not betting advice. Always verify independently. Safety first.")
