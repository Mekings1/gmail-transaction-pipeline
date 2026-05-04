"""
Dashboard Lambda

GET /dashboard?token=SECRET  →  Serves the HTML single-page dashboard
GET /api/data?token=SECRET   →  Returns aggregated JSON for charts
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME            = os.environ["DYNAMODB_TABLE_NAME"]
DASHBOARD_TOKEN_PARAM = os.environ["DASHBOARD_TOKEN_PARAM"]

_table         = boto3.resource("dynamodb").Table(TABLE_NAME)
_ssm           = boto3.client("ssm")
_cached_token  = None


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_token() -> str:
    global _cached_token
    if _cached_token is None:
        _cached_token = _ssm.get_parameter(
            Name=DASHBOARD_TOKEN_PARAM, WithDecryption=True
        )["Parameter"]["Value"]
    return _cached_token


def authorized(event: dict) -> bool:
    params = event.get("queryStringParameters") or {}
    return params.get("token", "") == get_token()


# ── Data layer ────────────────────────────────────────────────────────────────

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def scan_transactions(days: int = 365) -> list[dict]:
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    items, last_key = [], None

    while True:
        kwargs = {"FilterExpression": Attr("date").gte(cutoff)}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp     = _table.scan(**kwargs)
        items   += resp.get("Items", [])
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return sorted(items, key=lambda x: x.get("sk", ""), reverse=True)


def build_payload() -> dict:
    items = scan_transactions()

    if not items:
        return {
            "summary": {}, "monthly": [],
            "balance_trend": [], "breakdown": [], "recent": [],
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    credits = [i for i in items if i.get("transaction_type") == "credit"]
    debits  = [i for i in items if i.get("transaction_type") == "debit"]

    total_credits = sum(float(i.get("amount", 0)) for i in credits)
    total_debits  = sum(float(i.get("amount", 0)) for i in debits)

    latest_balance = next(
        (float(i["available_balance"]) for i in items if i.get("available_balance")), 0.0
    )

    # Monthly aggregates
    monthly: dict = defaultdict(lambda: {"credits": 0.0, "debits": 0.0})
    for item in items:
        month = item.get("date", "")[:7]
        if item.get("transaction_type") == "credit":
            monthly[month]["credits"] += float(item.get("amount", 0))
        else:
            monthly[month]["debits"] += float(item.get("amount", 0))

    monthly_list = [
        {"month": m, "credits": round(v["credits"], 2), "debits": round(v["debits"], 2)}
        for m, v in sorted(monthly.items())
    ]

    # Balance trend — latest balance per day, last 90 days
    daily_balance: dict = {}
    for item in sorted(items, key=lambda x: x.get("sk", "")):
        d = item.get("date", "")
        if item.get("available_balance") and d:
            daily_balance[d] = float(item["available_balance"])

    cutoff_90 = (datetime.now(tz=timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    balance_trend = [
        {"date": d, "balance": b}
        for d, b in sorted(daily_balance.items())
        if d >= cutoff_90
    ]

    # Spending breakdown by description — top 8 debit categories
    breakdown: dict = defaultdict(float)
    for item in debits:
        desc = (item.get("description") or "Other")[:45]
        breakdown[desc] += float(item.get("amount", 0))

    breakdown_list = sorted(
        [{"description": k, "amount": round(v, 2)} for k, v in breakdown.items()],
        key=lambda x: x["amount"],
        reverse=True,
    )[:8]

    # Recent transactions
    recent = [
        {
            "date":              i.get("date", ""),
            "transaction_type":  i.get("transaction_type", ""),
            "amount":            float(i.get("amount", 0)),
            "currency":          i.get("currency", "NGN"),
            "description":       i.get("description", ""),
            "reference_number":  i.get("reference_number", ""),
            "available_balance": float(i.get("available_balance", 0)),
        }
        for i in items[:20]
    ]

    return {
        "summary": {
            "total_credits":     round(total_credits, 2),
            "total_debits":      round(total_debits, 2),
            "net_flow":          round(total_credits - total_debits, 2),
            "latest_balance":    round(latest_balance, 2),
            "transaction_count": len(items),
        },
        "monthly":       monthly_list,
        "balance_trend": balance_trend,
        "breakdown":     breakdown_list,
        "recent":        recent,
        "generated_at":  datetime.now(tz=timezone.utc).isoformat(),
    }


# ── HTML ──────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vale — Transactions</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b}
  header{background:#2C3C90;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}
  header h1{font-size:1.25rem;font-weight:700;letter-spacing:-0.01em}
  header span{font-size:0.78rem;opacity:.75}
  .wrap{max-width:1200px;margin:0 auto;padding:24px 16px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:22px}
  .card{background:#fff;border-radius:12px;padding:18px 20px;box-shadow:0 1px 3px rgba(0,0,0,.07)}
  .card-label{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:#64748b;margin-bottom:6px}
  .card-value{font-size:1.45rem;font-weight:700}
  .card-sub{font-size:.72rem;color:#94a3b8;margin-top:4px}
  .green{color:#16a34a}.red{color:#dc2626}.blue{color:#2C3C90}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:22px}
  @media(max-width:720px){.grid2{grid-template-columns:1fr}}
  .ccard{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.07)}
  .ccard h2{font-size:.82rem;font-weight:600;color:#475569;text-transform:uppercase;letter-spacing:.04em;margin-bottom:14px}
  .cwrap{position:relative;height:230px}
  .full{grid-column:1/-1}
  .tcard{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.07);overflow-x:auto}
  .tcard h2{font-size:.82rem;font-weight:600;color:#475569;text-transform:uppercase;letter-spacing:.04em;margin-bottom:14px}
  table{width:100%;border-collapse:collapse;font-size:.83rem}
  th{text-align:left;padding:7px 10px;font-size:.7rem;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;border-bottom:1px solid #e2e8f0}
  td{padding:9px 10px;border-bottom:1px solid #f8fafc;vertical-align:middle}
  tr:last-child td{border:none}
  .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.68rem;font-weight:700;text-transform:uppercase}
  .badge.credit{background:#dcfce7;color:#16a34a}
  .badge.debit{background:#fee2e2;color:#dc2626}
  .amt.credit{color:#16a34a;font-weight:600}
  .amt.debit{color:#dc2626;font-weight:600}
  .loading{text-align:center;padding:80px;color:#94a3b8;font-size:1rem}
</style>
</head>
<body>
<header>
  <h1>📊 Vale Transaction Dashboard</h1>
  <span id="ts">Loading…</span>
</header>
<div class="wrap">
  <div id="app"><div class="loading">Fetching your transactions…</div></div>
</div>
<script>
const token = new URLSearchParams(location.search).get('token')||'';
const API   = location.origin + '/api/data?token=' + token;

const fmt = n => 'NGN ' + (+n).toLocaleString('en-NG',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtD = d => new Date(d+'T12:00:00Z').toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});

async function load(){
  try{
    const r = await fetch(API);
    if(!r.ok) throw new Error('HTTP '+r.status);
    render(await r.json());
  }catch(e){
    document.getElementById('app').innerHTML='<div class="loading" style="color:#dc2626">⚠ '+e.message+'</div>';
  }
}

function render(d){
  const {summary:s,monthly,balance_trend,breakdown,recent,generated_at}=d;
  const net=s.net_flow, netCls=net>=0?'green':'red';
  document.getElementById('ts').textContent='Updated '+new Date(generated_at).toLocaleTimeString();
  document.getElementById('app').innerHTML=`
    <div class="cards">
      <div class="card"><div class="card-label">Total Credits</div><div class="card-value green">${fmt(s.total_credits)}</div><div class="card-sub">${s.transaction_count} transactions (12 months)</div></div>
      <div class="card"><div class="card-label">Total Debits</div><div class="card-value red">${fmt(s.total_debits)}</div></div>
      <div class="card"><div class="card-label">Net Flow</div><div class="card-value ${netCls}">${net>=0?'+':''}${fmt(net)}</div></div>
      <div class="card"><div class="card-label">Available Balance</div><div class="card-value blue">${fmt(s.latest_balance)}</div></div>
    </div>
    <div class="grid2">
      <div class="ccard"><h2>Monthly Credits vs Debits</h2><div class="cwrap"><canvas id="mc"></canvas></div></div>
      <div class="ccard"><h2>Balance Trend — last 90 days</h2><div class="cwrap"><canvas id="bt"></canvas></div></div>
      <div class="ccard full"><h2>Spending Breakdown — top categories</h2><div class="cwrap" style="height:260px"><canvas id="bd"></canvas></div></div>
    </div>
    <div class="tcard"><h2>Recent Transactions</h2>
      <table><thead><tr><th>Date</th><th>Type</th><th>Amount</th><th>Description</th><th>Reference</th><th>Balance After</th></tr></thead>
      <tbody>${recent.map(t=>`<tr>
        <td>${fmtD(t.date)}</td>
        <td><span class="badge ${t.transaction_type}">${t.transaction_type}</span></td>
        <td class="amt ${t.transaction_type}">${t.transaction_type==='credit'?'+':'-'}${fmt(t.amount)}</td>
        <td>${t.description||'—'}</td>
        <td style="font-size:.75rem;color:#94a3b8">${t.reference_number||'—'}</td>
        <td style="color:#475569">${fmt(t.available_balance)}</td>
      </tr>`).join('')}</tbody></table>
    </div>`;

  const months=monthly.map(m=>{const[y,mo]=m.month.split('-');return new Date(y,mo-1).toLocaleString('default',{month:'short',year:'2-digit'})});
  new Chart(document.getElementById('mc'),{type:'bar',data:{labels:months,datasets:[
    {label:'Credits',data:monthly.map(m=>m.credits),backgroundColor:'#4ade80',borderRadius:4},
    {label:'Debits', data:monthly.map(m=>m.debits), backgroundColor:'#f87171',borderRadius:4}
  ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top'}},scales:{y:{ticks:{callback:v=>'₦'+Number(v).toLocaleString()},grid:{color:'#f8fafc'}},x:{grid:{display:false}}}}});

  new Chart(document.getElementById('bt'),{type:'line',data:{labels:balance_trend.map(b=>fmtD(b.date)),datasets:[{
    label:'Balance',data:balance_trend.map(b=>b.balance),borderColor:'#2C3C90',backgroundColor:'rgba(44,60,144,.08)',
    fill:true,tension:.35,pointRadius:2
  }]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{ticks:{callback:v=>'₦'+Number(v).toLocaleString()},grid:{color:'#f8fafc'}},x:{grid:{display:false},ticks:{maxTicksLimit:7,maxRotation:0}}}}});

  new Chart(document.getElementById('bd'),{type:'bar',data:{labels:breakdown.map(b=>b.description),datasets:[{
    label:'Amount (NGN)',data:breakdown.map(b=>b.amount),
    backgroundColor:['#2C3C90','#3b5cc8','#4e72e0','#6288f5','#7b9ef8','#94b4ff','#adcaff','#c6e0ff'],borderRadius:4
  }]},options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{callback:v=>'₦'+Number(v).toLocaleString()},grid:{color:'#f8fafc'}},y:{grid:{display:false}}}}});
}
load();
</script>
</body>
</html>"""


# ── Handler ───────────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    logger.info("Path: %s", event.get("rawPath"))

    if not authorized(event):
        return {"statusCode": 401, "body": "Unauthorized"}

    path = event.get("rawPath", "")

    if path.endswith("/dashboard"):
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": DASHBOARD_HTML,
        }

    if path.endswith("/api/data"):
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
            },
            "body": json.dumps(build_payload(), cls=DecimalEncoder),
        }

    return {"statusCode": 404, "body": "Not found"}