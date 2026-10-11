"""Generates the Python snippets for code nodes that do all date arithmetic, from spec values.
The snippets run in Freya's sandbox: they read and write `vars`, never raise, and contain no
double braces. The same functions run locally in tests (see `run_local`)."""
import json

# Runs inside the sandbox. Parameters are injected as a JSON literal named P.
_CORE = r'''
import datetime, json
P = json.loads(__P__)
HOL = set(P['holidays'])
WD = set(P['business_weekdays'])
NAMES = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
def d2s(d): return d.isoformat()
def say(d): return NAMES[d.weekday()] + ' ' + str(d.day) + ' ' + d.strftime('%B') + ' ' + str(d.year)
def is_bd(d): return (d.weekday() + 1) in WD and d2s(d) not in HOL
def next_bd(d, include=True):
    d = d if include else d + datetime.timedelta(days=1)
    while not is_bd(d): d += datetime.timedelta(days=1)
    return d
def add_bd(d, n):
    while n > 0:
        d += datetime.timedelta(days=1)
        if is_bd(d): n -= 1
    return d
def last_bd_of_week(d):
    end = d + datetime.timedelta(days=6 - d.weekday())
    while end >= d - datetime.timedelta(days=d.weekday()) and not is_bd(end): end -= datetime.timedelta(days=1)
    return end
def parse_now(s):
    s = str(s or '').strip().replace('Z', '')
    try: return datetime.datetime.fromisoformat(s[:16])
    except Exception: return datetime.datetime.fromisoformat(s[:10] + 'T00:00')
def band_for(value):
    for b in P['bands']:
        try:
            v = float(value)
            if (b.get('min') is None or v >= float(b['min'])) and (b.get('max') is None or v <= float(b['max'])): return b
        except Exception: pass
    return None
'''

_SETUP = r'''
try:
    now = parse_now(vars.get(P['now_variable']))
    today = now.date()
    vars['today_iso'] = d2s(today)
    vars['today_text'] = say(today)
    vars['now_time'] = now.strftime('%H:%M')
    lw = last_bd_of_week(today)
    if lw <= today: lw = last_bd_of_week(today + datetime.timedelta(days=7 - today.weekday()))
    vars['attempt1_date_iso'] = d2s(lw)
    vars['attempt1_date_text'] = say(lw)
    cb = []
    if is_bd(today): cb.append(today)
    cb.append(next_bd(today, include=False))
    vars['callback_days_text'] = ' or '.join(say(d) + ' (' + d2s(d) + ')' for d in cb)
    vars['callback_days_iso'] = ','.join(d2s(d) for d in cb)
    lines = []
    for i in range(0, 35):
        d = today + datetime.timedelta(days=i)
        if is_bd(d): tag = 'business day'
        else:
            nb = next_bd(d)
            tag = ('bank holiday' if d2s(d) in HOL else 'weekend') + ', NOT a business day; the next business day is ' + NAMES[nb.weekday()] + ' ' + d2s(nb)
        rel = 'today' if i == 0 else ('tomorrow' if i == 1 else '')
        wk = 'this week' if d.isocalendar()[1] == today.isocalendar()[1] else ('next week' if d.isocalendar()[1] == (today + datetime.timedelta(days=7)).isocalendar()[1] else '')
        lines.append(d2s(d) + ' ' + NAMES[d.weekday()] + (' (' + rel + ')' if rel else '') + (' [' + wk + ']' if wk else '') + ': ' + tag)
    vars['calendar_text'] = '\n'.join(lines)
    for b in P['bands']:
        dl = add_bd(today, int(b['deadline_business_days_after_today']))
        vars['deadline_' + b['name'] + '_iso'] = d2s(dl)
        vars['deadline_' + b['name'] + '_text'] = say(dl)
    vars['setup_state'] = 'ok'
except Exception as e:
    vars['setup_state'] = 'error'
    vars['setup_error'] = str(e)[:200]
    for k in ['today_iso','today_text','attempt1_date_iso','attempt1_date_text','callback_days_text','calendar_text']:
        vars.setdefault(k, '')
'''


def params(spec):
    return {
        "now_variable": spec.get("now_variable") or "now",
        "holidays": spec["calendar"]["holidays"],
        "business_weekdays": spec["calendar"]["business_weekdays"],
        "bands": [{k: b.get(k) for k in ("name", "field", "min", "max", "deadline_business_days_after_today", "persuasion_attempts")}
                  for b in spec["bands"]],
    }


def _inject(body, spec):
    src = _CORE.replace("__P__", repr(json.dumps(params(spec)).replace("}}", "} }").replace("{{", "{ {"))) + body
    assert "{{" not in src and "}}" not in src, "code must not contain double braces"
    return src


def setup_code(spec):
    return _inject(_SETUP, spec)


def run_local(code, variables):
    """Run a snippet the way the sandbox does: a dict named vars, nothing returned."""
    v = dict(variables)
    exec(code, {"vars": v})
    return v


# ---------------------------------------------------------------- per-product facts (after the account call)
_PREPARE = r'''
def parse_products():
    prods = vars.get('products')
    if isinstance(prods, str):
        t = prods.strip()
        try: prods = json.loads(t)
        except Exception:
            try: prods = json.loads(t.replace("'", '"').replace('True', 'true').replace('False', 'false').replace('None', 'null'))
            except Exception: prods = None
    if isinstance(prods, dict): prods = [prods]
    if not isinstance(prods, list) or not prods:
        found = dict()
        for k in list(vars.keys()):
            ks = str(k)
            pre = 'get_account_response.data.' + P['list_field'] + '.'
            if ks.startswith(pre):
                rest = ks[len(pre):].split('.', 1)
                if len(rest) == 2 and rest[0].isdigit():
                    found.setdefault(int(rest[0]), dict())[rest[1]] = vars.get(k)
        prods = [found[i] for i in sorted(found)]
    return [p for p in prods if isinstance(p, dict)]
def money(x):
    try:
        v = float(x)
        return ('%.2f' % v) if v != int(v) else str(int(v))
    except Exception: return str(x)
def fill(tpl, p):
    out = tpl
    for k, v in p.items(): out = out.replace('[' + str(k) + ']', str(v))
    return out
try:
    now = parse_now(vars.get(P['now_variable']))
    today = now.date()
    prods = parse_products()
    facts, sentences, ids = [], [], []
    for i, p in enumerate(prods):
        typ = str(p.get('type') or '')
        phrase = fill(P['product_names'].get(typ, typ.replace('_', ' ')), p)
        ff = P['floor_field'].get(typ)
        floor = p.get(ff) if ff else None
        b = band_for(p.get(P['band_field'])) or (P['bands'][-1] if P['bands'] else dict())
        dl = add_bd(today, int(b.get('deadline_business_days_after_today') or 0))
        attempts = int(b.get('persuasion_attempts') or 0)
        valid = []
        d = today
        while d <= dl:
            if is_bd(d): valid.append(say(d) + ' (' + d2s(d) + ')')
            d += datetime.timedelta(days=1)
        try: extra = derived(today, dl, p)
        except Exception: extra = dict()
        q = dict(p)
        q.update(dict(phrase=phrase, past_due=money(p.get('past_due')), floor=money(floor) if floor is not None else ''))
        sentences.append(fill(P['product_sentence'], q))
        pid = str(p.get('product_id') or ('p' + str(i + 1)))
        ids.append(pid)
        f = ['PRODUCT ' + str(i + 1) + ': ' + phrase + ' (product_id ' + pid + ')']
        f.append('  FULL past-due amount: ' + money(p.get('past_due')) + '. Days late: ' + str(p.get('days_late')) + ' (band ' + str(b.get('name')) + ').')
        if floor is not None:
            f.append('  FLOOR (' + str(ff).replace('_', ' ') + '): ' + money(floor) + '. Never record less than ' + money(floor) + ' or more than ' + money(p.get('past_due')) + '.')
        f.append('  DEADLINE (latest promise date): ' + say(dl) + ' (' + d2s(dl) + ').')
        f.append('  VALID PROMISE DATES (the only dates you may record): ' + '; '.join(valid) + '.')
        f.append('  Persuasion attempts allowed for this product: ' + str(attempts) + '.')
        for k, v in extra.items():
            if isinstance(v, datetime.date): f.append('  ' + k.replace('_', ' ').upper() + ': ' + say(v) + ' (' + d2s(v) + ').')
            elif v not in (None, ''): f.append('  ' + k.replace('_', ' ').upper() + ': ' + str(v))
        facts.append('\n'.join(f))
    vars['product_count'] = str(len(prods))
    vars['product_ids'] = ','.join(ids)
    vars['products_facts'] = ('Today: ' + say(today) + ' (' + d2s(today) + '), time ' + now.strftime('%H:%M') + '.\n\n' + '\n\n'.join(facts)) if prods else ''
    vars['statement_text'] = ' '.join(sentences)
    vars['account_state'] = 'ok' if prods else 'error'
except Exception as e:
    vars['account_state'] = 'error'
    vars['prepare_error'] = str(e)[:200]
    for k in ['products_facts', 'statement_text', 'product_count', 'product_ids']:
        vars.setdefault(k, '')
'''


def prepare_params(spec, list_field, product_sentence):
    p = params(spec)
    names = {k: v.replace("{", "[").replace("}", "]") for k, v in (spec["account"].get("product_names") or {}).items()}
    p.update({"list_field": list_field, "product_names": names, "floor_field": spec["amounts"].get("floor_field") or {},
              "band_field": (spec["bands"][0].get("field") if spec["bands"] else "days_late") or "days_late",
              "product_sentence": product_sentence})
    return p


def prepare_code(spec, list_field, product_sentence, derived_src):
    pj = json.dumps(prepare_params(spec, list_field, product_sentence)).replace("}}", "} }").replace("{{", "{ {")
    src = _CORE.replace("__P__", repr(pj)) + "\n" + derived_src + "\n" + _PREPARE
    assert "{{" not in src and "}}" not in src, "code must not contain double braces"
    compile(src, "prepare", "exec")
    return src


DERIVED_HELP = """Write a Python function, nothing else:

def derived(today, deadline, product):
    ...
    return {"name": value, ...}

`today` and `deadline` are datetime.date. `product` is the product dict from the account call.
Return one entry per derived date rule, keyed by its name, each a datetime.date.
Available helpers (already defined): is_bd(d) -> bool (business day), next_bd(d, include=True) -> date
(first business day on or after d; include=False starts the day after), add_bd(d, n) -> date (n-th business
day after d), last_bd_of_week(d) -> last business day of d's Monday-to-Sunday week, datetime module.
Weeks run Monday to Sunday. Do not write double curly braces anywhere. Do not import anything but datetime."""


def check_derived(spec, src, list_field="products"):
    """Run the derived() function for 60 consecutive days. Returns a list of problems."""
    import datetime
    code = prepare_code(spec, list_field, "[phrase]", src)
    problems = []
    for i in range(60):
        day = datetime.date(2026, 10, 1) + datetime.timedelta(days=i)
        v = run_local(code, {"now": day.isoformat() + "T11:00",
                             "products": [{"product_id": "p1", "type": "credit_card", "past_due": 100, "days_late": 5,
                                           "minimum_payment": 30, "instalment_amount": 30, "card_last4": "1234"}]})
        if v.get("account_state") != "ok":
            problems.append(f"{day}: {v.get('prepare_error')}")
    return problems[:5]
