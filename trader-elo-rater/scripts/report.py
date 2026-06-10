"""
Official rating report generator. Conforms to Trading League Rating Standard v2.
This is the skill's primary deliverable: every analysis ends in a PDF.

Two report modes, chosen automatically:
  - RATING report   : trader scored within a cohort (has league_rating + pillars).
  - TRACK-RECORD report : single account, no cohort yet -> metrics + eligibility,
                          clearly marked rating-pending.

Importable: generate_all(df, league, round_name, out_dir, trader=None).
CLI: python report.py --detail round_detail.csv --league "..." --round "..." [--trader N]
"""

import os
import argparse
import datetime as dt
import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Flowable)

INK = colors.HexColor("#14181F"); SLATE = colors.HexColor("#3A4350")
MUTED = colors.HexColor("#7A8593"); ACCENT = colors.HexColor("#1F7A8C")
GOLDC = colors.HexColor("#C8A24B"); LINE = colors.HexColor("#D9DEE5")
LIGHT = colors.HexColor("#F2F5F8"); GOOD = colors.HexColor("#2E7D5B"); BAD = colors.HexColor("#B23A48")

TIER_COLORS = {"Apex": colors.HexColor("#5B3A9B"), "Diamond": colors.HexColor("#2AA6C4"),
    "Gold": colors.HexColor("#C8A24B"), "Silver": colors.HexColor("#8A95A3"),
    "Bronze": colors.HexColor("#A36A3C"), "Provisional": colors.HexColor("#7A8593")}

PILLAR_LABELS = {"pillar_risk_adjusted": "Risk-adjusted return",
    "pillar_capital_preservation": "Capital preservation",
    "pillar_payoff_discipline": "Payoff & tail discipline", "pillar_return_alpha": "Return / alpha",
    "pillar_consistency": "Consistency", "pillar_behavioral_hygiene": "Behavioral hygiene"}

METRIC_GROUPS = [
    ("Return", [("net_return_pct","Net return","pct"),("net_pl","Net P/L","usd"),
                ("start_balance","Start balance","usd"),("end_balance","End balance","usd")]),
    ("Risk-adjusted", [("sortino","Sortino","num"),("sharpe","Sharpe","num"),
                       ("return_over_maxdd","Return / max DD","num")]),
    ("Preservation", [("max_drawdown","Max drawdown","pct"),("ulcer_index","Ulcer index","num"),
                      ("recovery_factor","Recovery factor","num")]),
    ("Payoff & tails", [("profit_factor","Profit factor","num"),("payoff_ratio","Payoff ratio","num"),
                        ("expectancy","Expectancy / trade","usd"),("win_rate","Win rate","pct"),
                        ("largest_loss_pct","Largest single loss","pct")]),
    ("Consistency", [("equity_linearity","Equity-curve linearity","num"),
                     ("trade_return_std","Trade-return dispersion","num")]),
    ("Hygiene", [("peak_leverage","Peak leverage","x"),("leverage_consistency","Sizing dispersion","num"),
                 ("trade_count","Trade count","int")]),
]


def fmt(v, kind):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if kind == "pct": return f"{v*100:,.2f}%"
    if kind == "usd": return f"${v:,.2f}"
    if kind == "x":   return f"{v:,.1f}x"
    if kind == "int": return f"{int(round(v))}"
    return f"{v:,.3f}"


class Bar(Flowable):
    def __init__(self, pct, width=70*mm, height=5*mm, color=ACCENT):
        super().__init__(); self.pct = max(0.0, min(1.0, float(pct)))
        self.width = width; self.height = height; self.color = color
    def draw(self):
        c = self.canv; c.setFillColor(LIGHT)
        c.roundRect(0, 0, self.width, self.height, 1.2, stroke=0, fill=1)
        c.setFillColor(self.color)
        c.roundRect(0, 0, max(self.width*self.pct, 1.2), self.height, 1.2, stroke=0, fill=1)


class Badge(Flowable):
    def __init__(self, text, bg, fg=colors.white, width=37*mm, height=8*mm, size=9):
        super().__init__(); self.text=text; self.bg=bg; self.fg=fg
        self.width=width; self.height=height; self.size=size
    def draw(self):
        c=self.canv; c.setFillColor(self.bg)
        c.roundRect(0,0,self.width,self.height,2,stroke=0,fill=1)
        c.setFillColor(self.fg); c.setFont("Helvetica-Bold",self.size)
        c.drawCentredString(self.width/2,self.height/2-self.size*0.35,self.text)


def _hf(canvas, doc, league):
    canvas.saveState(); w,h = A4
    canvas.setFillColor(INK); canvas.rect(0,h-26*mm,w,26*mm,stroke=0,fill=1)
    canvas.setFillColor(GOLDC); canvas.rect(0,h-26*mm,w,1.4*mm,stroke=0,fill=1)
    canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold",15)
    canvas.drawString(18*mm,h-13*mm,league.upper())
    canvas.setFillColor(GOLDC); canvas.setFont("Helvetica-Bold",8.5)
    canvas.drawString(18*mm,h-18.5*mm,"OFFICIAL RATING REPORT")
    canvas.setFillColor(MUTED); canvas.setFont("Helvetica",7.5)
    canvas.drawRightString(w-18*mm,10*mm,
        f"Generated {dt.date.today().isoformat()}  ·  Trading League Rating Standard v2  ·  Page {doc.page}")
    canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5)
    canvas.line(18*mm,13*mm,w-18*mm,13*mm); canvas.restoreState()


def _styles():
    s = getSampleStyleSheet()
    body = ParagraphStyle("B", parent=s["Normal"], textColor=SLATE, fontSize=9, leading=13)
    H = ParagraphStyle("H", parent=s["Heading2"], textColor=INK, fontName="Helvetica-Bold",
                       fontSize=11, spaceBefore=10, spaceAfter=5)
    small = ParagraphStyle("S", parent=s["Normal"], textColor=MUTED, fontSize=7.6, leading=10)
    return body, H, small


def _identity(story, row, round_name, body, small):
    story.append(Paragraph(f"<b>{row.get('trader','-')}</b>",
        ParagraphStyle("n", parent=body, fontSize=16, leading=20, textColor=INK, spaceAfter=2)))
    story.append(Paragraph(f"Account {row.get('account','-')}  ·  {round_name}", small))
    story.append(Spacer(1, 10))


def _metric_table(row):
    data = [["Group","Metric","Value"]]; spans=[]; r=1
    for group, metrics in METRIC_GROUPS:
        first=r
        for mk,ml,kind in metrics:
            if mk not in row: continue
            data.append([group if r==first else "", ml, fmt(row.get(mk), kind)]); r+=1
        if r>first: spans.append((first,r-1))
    mt = Table(data, colWidths=[30*mm,90*mm,54*mm], repeatRows=1)
    ts=[("FONT",(0,0),(-1,0),"Helvetica-Bold",8),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("BACKGROUND",(0,0),(-1,0),INK),("FONT",(0,1),(-1,-1),"Helvetica",8.5),
        ("TEXTCOLOR",(0,1),(0,-1),ACCENT),("FONT",(0,1),(0,-1),"Helvetica-Bold",8.5),
        ("TEXTCOLOR",(1,1),(1,-1),SLATE),("TEXTCOLOR",(2,1),(2,-1),INK),
        ("FONT",(2,1),(2,-1),"Helvetica-Bold",8.5),("ALIGN",(2,0),(2,-1),"RIGHT"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT]),("LINEBELOW",(0,0),(-1,0),0.6,INK),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),("LEFTPADDING",(0,0),(-1,-1),7)]
    for a,b in spans:
        ts.append(("SPAN",(0,a),(0,b))); ts.append(("VALIGN",(0,a),(0,b),"MIDDLE"))
    mt.setStyle(TableStyle(ts)); return mt


def _banner(story, eligible, dq_reason, body):
    if eligible:
        txt, bg = "ELIGIBLE — ranked on the official ladder", GOOD
    else:
        txt = f"DISQUALIFIED — {str(dq_reason).rstrip(';').replace(';', ', ')}  (excluded from the ranked ladder)"
        bg = BAD
    ban = Table([[Paragraph(f"<b>{txt}</b>",
        ParagraphStyle("ban", parent=body, textColor=colors.white, fontSize=8.5))]], colWidths=[174*mm])
    ban.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),bg),("LEFTPADDING",(0,0),(-1,-1),10),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    story.append(ban)


def build_rating_report(row, league, round_name, out_path):
    body, H, small = _styles()
    doc = SimpleDocTemplate(out_path, pagesize=A4, topMargin=32*mm, bottomMargin=18*mm,
                            leftMargin=18*mm, rightMargin=18*mm)
    story = []
    tier = str(row.get("tier","Provisional")); state = str(row.get("state","projected")).capitalize()
    eligible = bool(row.get("eligible", True)); lr = row.get("league_rating")
    _identity(story, row, round_name, body, small)

    lr_str = "-" if lr is None or pd.isna(lr) else f"{float(lr):,.0f}"
    big = ParagraphStyle("big", parent=body, fontSize=40, textColor=INK, fontName="Helvetica-Bold", leading=42)
    cap = ParagraphStyle("cap", parent=small, textColor=MUTED, fontSize=8)
    left = [Paragraph("LEAGUE RATING", cap), Paragraph(lr_str, big),
            Paragraph("R &minus; 2&middot;RD (conservative)", small)]
    badges = Table([[Badge(tier.upper(), TIER_COLORS.get(tier, MUTED)),
                     Badge(state.upper(), ACCENT if state=="Confirmed" else SLATE)]], colWidths=[37*mm,37*mm])
    badges.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(0,0),6),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    def g(k, f="{:,.0f}"):
        v = row.get(k); return "-" if v is None or pd.isna(v) else f.format(float(v))
    stat = Table([["Rank in round", str(int(row["rank"])) if not pd.isna(row.get("rank")) else "-"],
                  ["Glicko R", g("R")], ["Rating deviation", g("RD")], ["Composite", g("composite","{:.3f}")]],
                 colWidths=[30*mm,22*mm])
    stat.setStyle(TableStyle([("FONT",(0,0),(-1,-1),"Helvetica",8.5),("TEXTCOLOR",(0,0),(0,-1),MUTED),
        ("TEXTCOLOR",(1,0),(1,-1),INK),("FONT",(1,0),(1,-1),"Helvetica-Bold",8.5),("ALIGN",(1,0),(1,-1),"RIGHT"),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),("TOPPADDING",(0,0),(-1,-1),3),("LINEBELOW",(0,0),(-1,-2),0.4,LINE)]))
    head = Table([[left, [badges, Spacer(1,6), stat]]], colWidths=[90*mm,84*mm])
    head.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("BACKGROUND",(0,0),(-1,-1),colors.white),
        ("BOX",(0,0),(-1,-1),0.6,LINE),("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
        ("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12)]))
    story.append(head); story.append(Spacer(1,4))
    _banner(story, eligible, row.get("dq_reason",""), body)

    story.append(Paragraph("What this rating means", H))
    floor = ("a permanent uncertainty floor applies because track-record data cannot capture floating "
             "drawdown or verified execution, so the rating is capped near the Gold tier until results are "
             "confirmed through on-platform trading" if state=="Projected" else
             "verified on-platform data has narrowed the uncertainty band, so the displayed rating reflects "
             "the full confidence of the system and all tiers are reachable")
    story.append(Paragraph(f"The League Rating is a conservative figure: the Glicko rating minus two "
        f"deviations (R &minus; 2&middot;RD). It rewards results that are both strong and repeatable — luck "
        f"alone does not climb the ladder. This is a <b>{state.lower()}</b> rating, meaning {floor}.", body))

    story.append(Paragraph("Pillar breakdown (cohort percentile)", H))
    prows=[]
    for key,label in PILLAR_LABELS.items():
        if key not in row or pd.isna(row.get(key)): continue
        pct=float(row[key])
        prows.append([Paragraph(label, body), Bar(pct),
            Paragraph(f"{pct*100:,.0f}%", ParagraphStyle("pv",parent=body,alignment=TA_RIGHT,
                textColor=INK,fontName="Helvetica-Bold"))])
    if prows:
        pt=Table(prows, colWidths=[52*mm,92*mm,18*mm])
        pt.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("TOPPADDING",(0,0),(-1,-1),5),("LINEBELOW",(0,0),(-1,-2),0.3,LINE)]))
        story.append(pt)
    story.append(Paragraph("Weights: risk-adjusted 28% · preservation 28% · payoff 22% · return 10% · "
                           "consistency 7% · hygiene 5%.", small))

    story.append(Paragraph("Track-record metrics", H))
    story.append(_metric_table(row)); story.append(Spacer(1,8))
    story.append(Paragraph("Methodology: all metrics are recomputed from the raw MetaTrader 5 deals record; "
        "the broker summary is not used. Metrics are percentile-ranked within the round cohort, weighted into "
        "a composite, ranked, and converted to a Glicko-2 rating via virtual pairwise matches. Full "
        "definitions: Trading League Rating Standard v2.", small))
    doc.build(story, onFirstPage=lambda c,d:_hf(c,d,league), onLaterPages=lambda c,d:_hf(c,d,league))
    return out_path


def build_track_record_report(row, league, round_name, out_path):
    body, H, small = _styles()
    doc = SimpleDocTemplate(out_path, pagesize=A4, topMargin=32*mm, bottomMargin=18*mm,
                            leftMargin=18*mm, rightMargin=18*mm)
    story = []
    eligible = bool(row.get("eligible", True))
    _identity(story, row, round_name, body, small)

    big = ParagraphStyle("big", parent=body, fontSize=26, textColor=MUTED, fontName="Helvetica-Bold", leading=30)
    cap = ParagraphStyle("cap", parent=small, textColor=MUTED, fontSize=8)
    left = [Paragraph("LEAGUE RATING", cap), Paragraph("PENDING", big),
            Paragraph("assigned once scored in a cohort", small)]
    badges = Table([[Badge("TRACK RECORD", SLATE), Badge("UNRATED", MUTED)]], colWidths=[37*mm,37*mm])
    badges.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(0,0),6),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    head = Table([[left, badges]], colWidths=[90*mm,84*mm])
    head.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("BACKGROUND",(0,0),(-1,-1),colors.white),
        ("BOX",(0,0),(-1,-1),0.6,LINE),("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
        ("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12)]))
    story.append(head); story.append(Spacer(1,4))
    _banner(story, eligible, row.get("dq_reason",""), body)

    story.append(Paragraph("Status", H))
    story.append(Paragraph("This is a single-account track-record analysis. A competitive League Rating is "
        "relative by design — it only exists once this account is scored against the other traders in a "
        "round. Submit this account within a round cohort to convert these metrics into a rating, tier, and "
        "ladder position.", body))
    if not eligible:
        story.append(Paragraph("Note: this account would be <b>disqualified</b> from a round on the basis "
            "shown above, regardless of cohort.", body))

    story.append(Paragraph("Track-record metrics", H))
    story.append(_metric_table(row)); story.append(Spacer(1,8))
    story.append(Paragraph("Methodology: all metrics are recomputed from the raw MetaTrader 5 deals record; "
        "the broker summary is not used. Full definitions: Trading League Rating Standard v2.", small))
    doc.build(story, onFirstPage=lambda c,d:_hf(c,d,league), onLaterPages=lambda c,d:_hf(c,d,league))
    return out_path


def _safe(name, acct):
    s = "".join(ch for ch in str(name) if ch.isalnum() or ch in " _-").strip().replace(" ", "_")
    return f"rating_{s}_{acct}.pdf"


def generate_all(df, league, round_name, out_dir, trader=None):
    """Generate a PDF per trader. Auto-picks rating vs track-record mode per row."""
    if trader:
        df = df[df["trader"].astype(str).str.lower() == str(trader).lower()]
        if df.empty:
            raise SystemExit(f"Trader '{trader}' not found")
    os.makedirs(out_dir, exist_ok=True)
    made = []
    rated_mode = "league_rating" in df.columns
    for _, row in df.iterrows():
        out = os.path.join(out_dir, _safe(row.get("trader","trader"), row.get("account","")))
        if rated_mode and not pd.isna(row.get("league_rating")):
            build_rating_report(row, league, round_name, out)
        else:
            build_track_record_report(row, league, round_name, out)
        made.append(out)
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", required=True)
    ap.add_argument("--trader", default=None)
    ap.add_argument("--league", default="Trading League")
    ap.add_argument("--round", dest="round_name", default="Competition Round")
    ap.add_argument("--out-dir", default="./reports")
    a = ap.parse_args()
    df = pd.read_csv(a.detail)
    made = generate_all(df, a.league, a.round_name, a.out_dir, a.trader)
    print("Generated:\n  " + "\n  ".join(made))


if __name__ == "__main__":
    main()
