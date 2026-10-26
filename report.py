"""Detailed statistics, charts and HTML report for the collected traffic data."""
import html as htmllib
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

CATS = {0: "Neznáme", 1: "Nehoda", 2: "Hmla", 3: "Nebezpečenstvo", 4: "Dážď", 5: "Poľadovica",
        6: "Zápcha", 7: "Uzavretý pruh", 8: "Uzavretá cesta", 9: "Výluka / práce", 10: "Vietor",
        11: "Záplavy", 14: "Pokazené vozidlo"}
MAG = {0: "Neznáme", 1: "Malé", 2: "Stredné", 3: "Veľké", 4: "Uzávierka"}
DAYS = ["Po", "Ut", "St", "Št", "Pi", "So", "Ne"]
BUCKETS = [("Noc 00-05", 0, 5), ("Ráno 06-09", 6, 9), ("Doobeda 10-14", 10, 14),
           ("Popoludnie 15-19", 15, 19), ("Večer 20-23", 20, 23)]


def run_report(tz, outdir, make_html, flow_csv, inc_csv):
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    if not Path(flow_csv).exists():
        sys.exit("No data yet - run 'collect' first.")

    # ---------- load ----------
    f = pd.read_csv(flow_csv)
    f["ts"] = pd.to_datetime(f["ts"], utc=True).dt.tz_convert(tz)
    f = f[(f["free"] > 0) & f["cur"].notna()].copy()
    if f.empty:
        sys.exit("No usable measurements yet.")
    f["cong"] = (1 - f["cur"] / f["free"]).clip(0, 1) * 100
    f["delay_s"] = (f["cur_tt"] - f["free_tt"]).clip(lower=0)
    f["hour"] = f["ts"].dt.hour
    f["dow"] = f["ts"].dt.dayofweek
    f["date"] = f["ts"].dt.date

    # one row per measurement run (whole-area average)
    r = f.groupby("ts").agg(cong=("cong", "mean"), speed=("cur", "mean"), free=("free", "mean"),
                            delay=("delay_s", "mean"), worst=("cong", "max")).reset_index()
    r["hour"] = r["ts"].dt.hour
    r["dow"] = r["ts"].dt.dayofweek
    r["date"] = r["ts"].dt.date
    r["weekend"] = r["dow"] >= 5
    now = r["ts"].max()
    first = r["ts"].min()

    u = None
    if Path(inc_csv).exists():
        inc = pd.read_csv(inc_csv).dropna(subset=["id"])
        if len(inc):
            inc["ts"] = pd.to_datetime(inc["ts"], utc=True).dt.tz_convert(tz)
            inc["cat"] = inc["cat"].map(CATS).fillna("Iné")
            inc["magnitude"] = inc["magnitude"].map(MAG).fillna("Neznáme")
            inc["roads"] = inc["roads"].fillna("")
            u = inc.groupby("id").agg(first=("ts", "min"), last=("ts", "max"), cat=("cat", "first"),
                                      mag=("magnitude", "first"), delay=("delay_s", "max"),
                                      length=("length_m", "max"), roads=("roads", "first"),
                                      frm=("from", "first"), to=("to", "first"))
            u["dur_min"] = (u["last"] - u["first"]).dt.total_seconds() / 60
            u["hour"] = u["first"].dt.hour
            u["dow"] = u["first"].dt.dayofweek
            u["date"] = u["first"].dt.date
            u["delay"] = u["delay"].fillna(0)

    sections = []

    def add(title, build):
        try:
            res = build()
            if res:
                res["title"] = title
                sections.append(res)
        except Exception as e:  # keep the report going if one part fails on little data
            print(f"[skipped: {title}: {e!r}]", file=sys.stderr)

    def save(fig, name):
        fig.tight_layout()
        fig.savefig(out / name, dpi=110)
        plt.close(fig)
        return name

    def heat(ax, fig, mat, xl, yl, title):
        im = ax.imshow(mat, aspect="auto", cmap="RdYlGn_r")
        ax.set_xticks(range(len(xl)), xl)
        ax.set_yticks(range(len(yl)), yl)
        ax.set_title(title)
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                if not np.isnan(mat[i, j]):
                    ax.text(j, i, f"{mat[i, j]:.0f}", ha="center", va="center", fontsize=6)
        fig.colorbar(im, ax=ax)

    # ---------- KPIs ----------
    last = r.iloc[-1]
    hist = r[(r["dow"] == last["dow"]) & (r["hour"] == last["hour"]) & (r["ts"] < last["ts"])]
    usual = f"{hist['cong'].mean():.1f} %" if len(hist) else "zatiaľ málo dát"
    expected = max(1, int((now - first).total_seconds() // 1800) + 1)
    worst_run = r.loc[r["cong"].idxmax()]
    kpis = [
        ("Obdobie", f"{first:%d.%m.%Y} – {now:%d.%m.%Y}"),
        ("Meraní (behov)", f"{len(r)}"),
        ("Meracích bodov", f"{f['point'].nunique()}"),
        ("Pokrytie dát", f"{min(100, len(r) / expected * 100):.0f} %"),
        ("Priemerná zápcha", f"{r['cong'].mean():.1f} %"),
        ("Priemerná rýchlosť", f"{f['cur'].mean():.0f} km/h"),
        ("Rýchlosť voľnej cesty", f"{f['free'].mean():.0f} km/h"),
        ("Index rýchlosti", f"{f['cur'].mean() / f['free'].mean() * 100:.0f} %"),
        ("Priem. zdržanie na úseku", f"{f['delay_s'].mean():.0f} s"),
        ("Meraní so zápchou ≥25 %", f"{(f['cong'] >= 25).mean() * 100:.1f} %"),
        ("Meraní s ťažkou zápchou ≥50 %", f"{(f['cong'] >= 50).mean() * 100:.1f} %"),
        ("Najhoršie meranie", f"{worst_run['cong']:.1f} % ({worst_run['ts']:%d.%m. %H:%M})"),
        ("Teraz", f"{last['cong']:.1f} % ({last['ts']:%d.%m. %H:%M})"),
        ("Obvykle v tomto čase", usual),
        ("Uzávierky (merania)", f"{int((f['closed'] == True).sum())}"),
    ]
    if u is not None:
        kpis += [("Incidentov (unikátnych)", f"{len(u)}"),
                 ("Zdržanie z incidentov", f"{u['delay'].sum() / 3600:.1f} h")]

    # ---------- short term ----------
    def s_7days():
        d = r[r["ts"] > now - pd.Timedelta(days=7)]
        if d.empty:
            return None
        s = d.set_index("ts")["cong"].resample("1h").mean()
        fig, ax = plt.subplots(figsize=(11, 3.8))
        ax.plot(s.index, s.values, lw=1, label="hodinový priemer")
        ax.plot(s.index, s.rolling(24, min_periods=6).mean(), lw=2, label="24h kĺzavý priemer")
        ax.set_title("Zápcha %, posledných 7 dní")
        ax.set_ylabel("%")
        ax.legend()
        return dict(img=save(fig, "last7days.png"))

    def s_24h():
        d = r[r["ts"] > now - pd.Timedelta(hours=24)]
        if d.empty:
            return None
        t = d.groupby("hour").agg(a=("cong", "mean"), b=("worst", "max"), c=("speed", "mean"),
                                  e=("delay", "mean"), n=("cong", "size"))
        t.columns = ["Zápcha %", "Max. úsek %", "Rýchlosť km/h", "Zdržanie s", "Meraní"]
        t.index = [f"{h:02d}:00" for h in t.index]
        return dict(df=t)

    def s_days7():
        d = r[r["ts"] > now - pd.Timedelta(days=7)]
        if d.empty:
            return None
        hourly = d.groupby(["date", "hour"])["cong"].mean().reset_index()
        peak = hourly.loc[hourly.groupby("date")["cong"].idxmax()].set_index("date")
        t = d.groupby("date").agg(a=("cong", "mean"), b=("speed", "mean"), c=("worst", "max"))
        t["peak_h"] = peak["hour"].map(lambda h: f"{h:02d}:00")
        t["peak_c"] = peak["cong"]
        t.columns = ["Zápcha %", "Rýchlosť km/h", "Max. úsek %", "Špička o", "Špička %"]
        t.index = [f"{x:%d.%m.} {DAYS[x.weekday()]}" for x in t.index]
        return dict(df=t)

    add("Priebeh posledných 7 dní", s_7days)
    add("Posledných 24 hodín po hodinách", s_24h)
    add("Posledných 7 dní po dňoch", s_days7)

    # ---------- trends ----------
    def s_daily():
        d = r.groupby("date")["cong"].mean()
        fig, ax = plt.subplots(figsize=(11, 3.8))
        ax.bar(range(len(d)), d.values, alpha=.6)
        if len(d) >= 3:
            ax.plot(range(len(d)), d.rolling(7, min_periods=2).mean().values, color="red", lw=2,
                    label="7-dňový priemer")
            ax.legend()
        step = max(1, len(d) // 12)
        ax.set_xticks(range(0, len(d), step), [f"{x:%d.%m.}" for x in list(d.index)[::step]], rotation=45)
        ax.set_title("Denný priemer zápch %")
        return dict(img=save(fig, "daily.png"))

    def s_weekly():
        wk = r["ts"].dt.strftime("%G-W%V")
        t = r.groupby(wk).agg(a=("cong", "mean"), b=("speed", "mean"), c=("worst", "max"), n=("cong", "size"))
        t["d"] = t["a"].diff()
        t.columns = ["Zápcha %", "Rýchlosť km/h", "Max. úsek %", "Meraní", "Zmena oproti min. týždňu"]
        return dict(df=t)

    def s_monthly():
        t = r.groupby(r["ts"].dt.strftime("%Y-%m")).agg(a=("cong", "mean"), b=("speed", "mean"),
                                                         c=("worst", "max"), n=("cong", "size"))
        t["d"] = t["a"].diff()
        t.columns = ["Zápcha %", "Rýchlosť km/h", "Max. úsek %", "Meraní", "Zmena oproti min. mesiacu"]
        return dict(df=t)

    add("Denný trend", s_daily)
    add("Týždenné porovnanie", s_weekly)
    add("Mesačné porovnanie", s_monthly)

    # ---------- patterns ----------
    def s_heat():
        p = r.pivot_table(index="dow", columns="hour", values="cong", aggfunc="mean")
        p = p.reindex(index=range(7), columns=range(24))
        fig, ax = plt.subplots(figsize=(12, 4))
        heat(ax, fig, p.values, [f"{h:02d}" for h in range(24)], DAYS, "Zápcha % podľa dňa a hodiny")
        p.index = DAYS
        p.columns = [f"{h:02d}" for h in range(24)]
        return dict(df=p, img=save(fig, "heatmap.png"))

    def s_profile():
        fig, ax = plt.subplots(figsize=(11, 3.8))
        rows = {}
        for name, mask, col in (("Pracovné dni", ~r["weekend"], "tab:blue"), ("Víkend", r["weekend"], "tab:orange")):
            g = r[mask].groupby("hour")["cong"]
            if g.ngroups == 0:
                continue
            m, lo, hi, p90 = g.mean(), g.quantile(.25), g.quantile(.75), g.quantile(.9)
            ax.plot(m.index, m.values, color=col, lw=2, label=name)
            ax.fill_between(m.index, lo.values, hi.values, color=col, alpha=.2)
            rows[f"{name} – priemer %"] = m
            rows[f"{name} – p90 %"] = p90
        ax.set_xticks(range(0, 24, 2))
        ax.set_title("Denný profil: priemer a rozpätie 25.–75. percentil")
        ax.set_xlabel("hodina")
        ax.legend()
        t = pd.DataFrame(rows)
        t.index = [f"{h:02d}:00" for h in t.index]
        return dict(df=t, img=save(fig, "profile.png"))

    def s_weekday():
        h = r.groupby(["dow", "hour"])["cong"].mean().reset_index()
        pk = h.loc[h.groupby("dow")["cong"].idxmax()].set_index("dow")
        t = r.groupby("dow").agg(a=("cong", "mean"), b=("speed", "mean"), c=("delay", "mean"), n=("cong", "size"))
        t["pk"] = pk["hour"].map(lambda x: f"{x:02d}:00")
        t["pkc"] = pk["cong"]
        t.columns = ["Zápcha %", "Rýchlosť km/h", "Zdržanie s", "Meraní", "Špička o", "Špička %"]
        fig, ax = plt.subplots(figsize=(7, 3.5))
        ax.bar([DAYS[i] for i in t.index], t["Zápcha %"])
        ax.set_title("Priemerná zápcha % podľa dňa v týždni")
        t.index = [DAYS[i] for i in t.index]
        return dict(df=t, img=save(fig, "weekday.png"))

    def s_buckets():
        rows = {}
        for name, a0, a1 in BUCKETS:
            d = r[(r["hour"] >= a0) & (r["hour"] <= a1)]
            rows[name] = {"Pracovné dni %": d[~d["weekend"]]["cong"].mean(),
                          "Víkend %": d[d["weekend"]]["cong"].mean(),
                          "Rýchlosť km/h": d["speed"].mean(), "Zdržanie s": d["delay"].mean()}
        return dict(df=pd.DataFrame(rows).T)

    def slots(best):
        g = r.groupby(["dow", "hour"])["cong"].agg(["mean", "count"])
        g = g.nsmallest(10, "mean") if best else g.nlargest(10, "mean")
        g.index = [f"{DAYS[d]} {h:02d}:00" for d, h in g.index]
        g.columns = ["Zápcha %", "Meraní"]
        return dict(df=g)

    add("Heatmapa: deň × hodina", s_heat)
    add("Denný profil (pracovné dni vs. víkend)", s_profile)
    add("Podľa dňa v týždni", s_weekday)
    add("Časti dňa", s_buckets)
    add("Kedy je premávka najhoršia (10 časových okien)", lambda: slots(False))
    add("Kedy je premávka najlepšia (10 časových okien)", lambda: slots(True))
    add("Najhoršie jednotlivé merania", lambda: dict(
        df=r.nlargest(10, "cong")[["ts", "cong", "worst", "speed"]].assign(
            ts=lambda x: x["ts"].dt.strftime("%d.%m.%Y %H:%M")).set_index("ts").rename(
            columns={"cong": "Zápcha %", "worst": "Max. úsek %", "speed": "Rýchlosť km/h"})))

    # ---------- points ----------
    pt = f.groupby("point").agg(lat=("lat", "first"), lon=("lon", "first"), cong=("cong", "mean"),
                                p90=("cong", lambda x: x.quantile(.9)), heavy=("cong", lambda x: (x >= 50).mean() * 100),
                                speed=("cur", "mean"), free=("free", "mean"), delay=("delay_s", "mean"),
                                n=("cong", "size"))
    ph = f.groupby(["point", "hour"])["cong"].mean().reset_index()
    pt["worst_h"] = ph.loc[ph.groupby("point")["cong"].idxmax()].set_index("point")["hour"].map(lambda h: f"{h:02d}:00")
    pt = pt.sort_values("cong", ascending=False)

    def s_points():
        t = pt[["lat", "lon", "cong", "p90", "heavy", "speed", "free", "delay", "worst_h", "n"]].copy()
        t.columns = ["Šírka", "Dĺžka", "Zápcha %", "p90 %", "Ťažká ≥50 % času", "Rýchlosť", "Voľná cesta",
                     "Zdržanie s", "Najhoršia hodina", "Meraní"]
        fig, ax = plt.subplots(figsize=(6.5, 6))
        sc = ax.scatter(pt["lon"], pt["lat"], c=pt["cong"], s=700, cmap="RdYlGn_r", edgecolors="k")
        for pid, row in pt.iterrows():
            ax.annotate(f"{pid}\n{row['cong']:.0f}%", (row["lon"], row["lat"]), ha="center", va="center", fontsize=7)
        ax.set_aspect(1 / np.cos(np.radians(pt["lat"].mean())))
        ax.set_title("Meracie body: priemerná zápcha %")
        fig.colorbar(sc, ax=ax)
        return dict(df=t, img=save(fig, "points_map.png"))

    def s_points_hour():
        p = f.pivot_table(index="point", columns="hour", values="cong", aggfunc="mean").reindex(
            index=pt.index, columns=range(24))
        fig, ax = plt.subplots(figsize=(12, 1 + 0.5 * len(p)))
        heat(ax, fig, p.values, [f"{h:02d}" for h in range(24)], list(p.index), "Zápcha % podľa bodu a hodiny")
        return dict(img=save(fig, "points_hour.png"))

    add("Meracie body", s_points)
    add("Body × hodina", s_points_hour)

    # ---------- distribution ----------
    def s_dist():
        q = f["cong"].quantile([.5, .75, .9, .95, .99])
        t = pd.DataFrame({"Zápcha %": list(q.values) + [f["cong"].max()]},
                         index=["medián", "75. percentil", "90. percentil", "95. percentil", "99. percentil", "maximum"])
        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.hist(f["cong"], bins=25)
        ax.axvline(q[.9], color="red", ls="--", label="p90")
        ax.set_yscale("log")
        ax.set_title("Rozdelenie zápch (jednotlivé merania, log os)")
        ax.set_xlabel("zápcha %")
        ax.legend()
        return dict(df=t, img=save(fig, "distribution.png"))

    add("Rozdelenie zápch", s_dist)

    # ---------- incidents ----------
    if u is not None:
        def s_inc_type():
            t = u.groupby("cat").agg(a=("delay", "size"), b=("delay", "mean"), c=("delay", "sum"), d=("dur_min", "mean"))
            t["b"] /= 60
            t["c"] /= 3600
            t.columns = ["Počet", "Priem. zdržanie min", "Spolu zdržanie h", "Priem. trvanie min"]
            return dict(df=t.sort_values("Počet", ascending=False))

        def s_inc_mag():
            return dict(df=u["mag"].value_counts().rename("Počet").to_frame())

        def s_inc_charts():
            fig, axs = plt.subplots(1, 3, figsize=(14, 3.8))
            u["cat"].value_counts().plot.barh(ax=axs[0])
            axs[0].set_title("Podľa typu")
            h = u["hour"].value_counts().reindex(range(24), fill_value=0)
            axs[1].bar(h.index, h.values)
            axs[1].set_title("Začiatok incidentu podľa hodiny")
            rd = u[u["roads"] != ""].groupby("roads").size().nlargest(8)
            if len(rd):
                rd.sort_values().plot.barh(ax=axs[2])
            axs[2].set_title("Najčastejšie cesty")
            return dict(img=save(fig, "incidents.png"))

        def s_inc_day():
            t = u.groupby("dow").agg(a=("delay", "size"), b=("delay", "sum")).reindex(range(7), fill_value=0)
            t["b"] /= 3600
            t.columns = ["Počet", "Zdržanie h"]
            t.index = DAYS
            return dict(df=t)

        def s_inc_roads():
            x = u.copy()
            x["roads"] = x["roads"].replace("", "(bez čísla cesty)")
            t = x.groupby("roads").agg(a=("delay", "size"), b=("delay", "sum"), c=("length", "sum"))
            t["b"] /= 3600
            t["c"] /= 1000
            t.columns = ["Počet", "Zdržanie h", "Dĺžka km spolu"]
            return dict(df=t.sort_values("Počet", ascending=False).head(15))

        def s_inc_top():
            t = u.nlargest(10, "delay")[["first", "cat", "roads", "frm", "to", "delay", "length", "dur_min"]].copy()
            t["first"] = t["first"].dt.strftime("%d.%m. %H:%M")
            t["delay"] /= 60
            t["length"] /= 1000
            t.columns = ["Začiatok", "Typ", "Cesta", "Od", "Do", "Zdržanie min", "Dĺžka km", "Trvanie min"]
            return dict(df=t.reset_index(drop=True), index=False)

        def s_inc_now():
            t = u[u["last"] == u["last"].max()].copy()
            t["delay"] /= 60
            t = t[["cat", "mag", "roads", "frm", "to", "delay"]]
            t.columns = ["Typ", "Závažnosť", "Cesta", "Od", "Do", "Zdržanie min"]
            return dict(note=f"Stav z merania {u['last'].max():%d.%m.%Y %H:%M}", df=t.reset_index(drop=True), index=False)

        add("Incidenty: grafy", s_inc_charts)
        add("Incidenty podľa typu", s_inc_type)
        add("Incidenty podľa závažnosti", s_inc_mag)
        add("Incidenty podľa dňa v týždni", s_inc_day)
        add("Cesty s najviac incidentmi", s_inc_roads)
        add("10 incidentov s najväčším zdržaním", s_inc_top)
        add("Aktívne incidenty (posledné meranie)", s_inc_now)

    # ---------- print ----------
    def fmt(x):
        return f"{x:.1f}"

    print(f"\nÚdaje: {first:%Y-%m-%d %H:%M} -> {now:%Y-%m-%d %H:%M} ({tz})")
    print("Zápcha % = o koľko je aktuálna rýchlosť nižšia než pri voľnej ceste (0 = voľno).\n")
    print("=== KĽÚČOVÉ ČÍSLA ===")
    for k, v in kpis:
        print(f"{k:34s} {v}")
    for s in sections:
        print(f"\n=== {s['title']} ===")
        if s.get("note"):
            print(s["note"])
        if s.get("df") is not None:
            print(s["df"].to_string(float_format=fmt, index=s.get("index", True)))
    print(f"\nGrafy uložené do {out}/")

    # ---------- html ----------
    if make_html:
        e = htmllib.escape
        parts = []
        for s in sections:
            parts.append(f"<h2>{e(s['title'])}</h2>")
            if s.get("note"):
                parts.append(f"<p>{e(s['note'])}</p>")
            if s.get("img"):
                parts.append(f'<img src="{s["img"]}" alt="{e(s["title"])}">')
            if s.get("df") is not None:
                parts.append('<div class="w">' + s["df"].to_html(
                    border=0, classes="t", index=s.get("index", True), float_format=fmt, na_rep="–") + "</div>")
        cards = "".join(f'<div class="c"><span>{e(k)}</span><b>{e(v)}</b></div>' for k, v in kpis)
        updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        page = f"""<!doctype html>
<html lang="sk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bratislava – živá premávka</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 0 auto; padding: 16px; line-height: 1.4; }}
img {{ max-width: 100%; height: auto; background: #fff; border-radius: 8px; }}
.g {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; }}
.c {{ background: rgba(128,128,128,.15); border-radius: 10px; padding: 10px 12px; }}
.c span {{ display: block; font-size: 12px; opacity: .75; }}
.c b {{ font-size: 18px; }}
.w {{ overflow-x: auto; }}
.t {{ border-collapse: collapse; font-size: 13px; }}
.t th, .t td {{ padding: 4px 10px; text-align: right; border-bottom: 1px solid rgba(128,128,128,.3); white-space: nowrap; }}
.t th:first-child, .t td:first-child {{ text-align: left; }}
</style></head><body>
<h1>Bratislava – živá premávka</h1>
<p>Aktualizované: {updated}. Zápcha % = o koľko je rýchlosť nižšia než pri voľnej ceste (0 % = voľno).</p>
<h2>Kľúčové čísla</h2><div class="g">{cards}</div>
{''.join(parts)}
</body></html>"""
        (out / "index.html").write_text(page, encoding="utf-8")
        print(f"HTML stránka: {out}/index.html")
