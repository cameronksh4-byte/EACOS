"""Generate Google Ads Editor import CSVs for Valley Agent Works and validate limits."""
import csv, sys, os

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
CAMPAIGN = "VAW | Search | AI Automation | Book a Call"
URL = "https://valleyagentworks.com/"

ad_groups = {
    "AI Automation Services": {
        "keywords": {
            "Exact": ["ai automation services", "ai automation company", "ai automation agency",
                      "business automation services", "ai consultant near me",
                      "automation consultant near me", "ai consulting for small business"],
            "Phrase": ["ai automation services", "ai automation for small business",
                       "business process automation services", "workflow automation consultant",
                       "ai consulting for small business", "hire ai automation",
                       "ai automation consultant"],
        },
        "paths": ("AI-Automation", "Free-Call"),
        "headlines": [
            "AI Automation for Business", "Valley Agent Works", "Book a Free Strategy Call",
            "Save Hours Every Week", "Automate Repetitive Tasks", "Built for Small Businesses",
            "Custom AI Agents & Workflows", "AI Automation Services", "Plain-English, No Jargon",
            "Automate Follow-Ups & Admin", "Free Consultation Call", "Connect the Tools You Use",
            "Fewer Spreadsheets, More Time", "Talk to the Builder Directly", "Hands-On AI Help",
        ],
        "descriptions": [
            "We build AI agents and automations that handle the busywork so you can focus on customers.",
            "Book a free call. We'll map one process to automate and show you what it could save.",
            "Lead follow-up, scheduling, data entry, reports: automated with tools you already use.",
            "Honest advice from a hands-on builder. If automation isn't a fit, we'll tell you.",
        ],
    },
    "Custom AI Agents": {
        "keywords": {
            "Exact": ["custom ai agent", "ai agent development", "hire ai agent developer",
                      "ai agents for small business", "ai agent for business"],
            "Phrase": ["custom ai agent", "ai agent development", "ai agent developer",
                       "build ai agent for business", "ai agents for small business",
                       "ai assistant for business", "ai chatbot for small business"],
        },
        "paths": ("AI-Agents", "Free-Call"),
        "headlines": [
            "Custom AI Agents for Business", "AI Agent Development", "Valley Agent Works",
            "Book a Free Strategy Call", "AI Agents That Do Real Work", "Respond to Leads Faster",
            "Automate Customer Follow-Up", "Tailored to Your Workflow", "Built for Small Businesses",
            "Plain-English, No Jargon", "Hire an AI Agent Developer", "Connect to Your CRM & Email",
            "From Idea to Working Agent", "Free Consultation Call", "Talk to the Builder Directly",
        ],
        "descriptions": [
            "Custom AI agents that answer leads, sort email, update your CRM and draft replies for you.",
            "Book a free call. We'll find one task an AI agent could take off your plate this month.",
            "Built around how your business already works, with clear scope and plain-English updates.",
            "Honest advice from a hands-on builder. If an AI agent isn't a fit, we'll tell you.",
        ],
    },
    "Zapier Make n8n Experts": {
        "keywords": {
            "Exact": ["zapier expert", "zapier consultant", "hire zapier expert", "make.com expert",
                      "n8n consultant", "n8n expert", "workflow automation expert"],
            "Phrase": ["zapier expert", "zapier consultant", "make.com consultant", "n8n consultant",
                       "n8n developer", "workflow automation expert"],
        },
        "paths": ("Automation", "Zapier-Make"),
        "headlines": [
            "Zapier & Make Automation Help", "n8n & Zapier Expert", "Valley Agent Works",
            "Fix Broken Zaps & Workflows", "Connect Your Apps & Data", "Book a Free Strategy Call",
            "Automate Repetitive Tasks", "Built for Small Businesses", "Add AI to Your Workflows",
            "Save Hours Every Week", "Plain-English, No Jargon", "Free Consultation Call",
            "Workflow Automation Expert", "Talk to the Builder Directly", "Stop Copy-Pasting Data",
        ],
        "descriptions": [
            "Zapier, Make and n8n workflows built, fixed and documented so they keep running.",
            "Book a free call. Bring your messiest process and we'll map out how to automate it.",
            "Add AI steps to your automations: summarize, sort, draft replies and pull out data.",
            "Honest advice from a hands-on builder. If automation isn't a fit, we'll tell you.",
        ],
    },
    "Brand": {
        "keywords": {
            "Exact": ["valley agent works", "valleyagentworks", "valley agentworks"],
            "Phrase": ["valley agent works"],
        },
        "paths": ("Book-A-Call", ""),
        "headlines": [
            "Valley Agent Works", "Official Site", "AI Automation for Business",
            "Book a Free Strategy Call", "Custom AI Agents & Workflows", "Built for Small Businesses",
            "Save Hours Every Week", "Plain-English, No Jargon", "Talk to the Builder Directly",
            "Free Consultation Call",
        ],
        "descriptions": [
            "We build AI agents and automations that handle the busywork so you can focus on customers.",
            "Book a free call. We'll map one process to automate and show you what it could save.",
            "Honest advice from a hands-on builder. If automation isn't a fit, we'll tell you.",
        ],
    },
}

negatives = {
    # Job seekers / learners / DIY
    "Phrase": ["jobs", "job", "career", "careers", "salary", "internship", "resume", "remote work",
               "course", "courses", "certification", "certificate", "training", "tutorial", "learn",
               "how to", "youtube", "udemy", "coursera", "reddit", "github", "open source", "pdf",
               "what is", "definition", "meaning", "examples", "free download", "download", "login",
               "sign in", "template", "templates", "self hosted", "self host",
               # Wannabe agency founders
               "start", "starting", "become", "side hustle", "make money", "passive income", "income",
               "skool", "mentor", "mentorship", "niche", "business ideas",
               # Wrong kind of "agent"
               "insurance", "real estate", "realtor", "travel agent", "talent agent", "sports agent",
               "secret agent", "fbi", "cia", "booking agent", "estate agent", "agent license",
               # Wrong kind of "automation"
               "home automation", "smart home", "alexa", "industrial automation", "plc", "scada",
               "robotics", "robot", "test automation", "selenium", "qa automation",
               # Money / scams
               "stock", "stocks", "crypto", "trading bot", "forex", "invest"],
    "Exact": ["chatgpt", "zapier", "make.com", "n8n", "ai", "ai agent", "ai automation"],
}

def check(label, items, limit):
    bad = [(s, len(s)) for s in items if len(s) > limit]
    if bad:
        print(f"OVER LIMIT {label}: {bad}")
        return False
    return True

ok = True
for ag, d in ad_groups.items():
    ok &= check(f"{ag} headlines", d["headlines"], 30)
    ok &= check(f"{ag} descriptions", d["descriptions"], 90)
    ok &= check(f"{ag} paths", [p for p in d["paths"]], 15)
    assert len(d["headlines"]) == len(set(d["headlines"])), f"dup headline in {ag}"
    assert 3 <= len(d["headlines"]) <= 15 and 2 <= len(d["descriptions"]) <= 4
if not ok:
    sys.exit(1)

with open(f"{OUT}/keywords.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Campaign", "Ad Group", "Keyword", "Criterion Type", "Final URL"])
    n = 0
    for ag, d in ad_groups.items():
        for mt, kws in d["keywords"].items():
            for k in kws:
                w.writerow([CAMPAIGN, ag, k, mt, URL]); n += 1
    print("keywords:", n)

with open(f"{OUT}/negative_keywords.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Campaign", "Ad Group", "Keyword", "Criterion Type"])
    n = 0
    for mt, kws in negatives.items():
        for k in kws:
            w.writerow([CAMPAIGN, "", k, f"Negative {mt}"]); n += 1
    print("negatives:", n)

with open(f"{OUT}/responsive_search_ads.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Campaign", "Ad Group", "Ad type"] + [f"Headline {i}" for i in range(1, 16)]
               + [f"Description {i}" for i in range(1, 5)] + ["Path 1", "Path 2", "Final URL"])
    for ag, d in ad_groups.items():
        h = d["headlines"] + [""] * (15 - len(d["headlines"]))
        ds = d["descriptions"] + [""] * (4 - len(d["descriptions"]))
        w.writerow([CAMPAIGN, ag, "Responsive search ad"] + h + ds + list(d["paths"]) + [URL])
    print("ads:", len(ad_groups))

# Assets
sitelinks = [("Book a Free Call", "Pick a time that works for you", "See if automation fits"),
             ("What We Automate", "Lead follow-up, admin, reports", "Real examples, plain English"),
             ("Custom AI Agents", "Agents built for your workflow", "Connected to tools you use"),
             ("About Valley Agent Works", "Meet the builder", "How we work with clients")]
callouts = ["Free Consultation", "Small Business Focused", "Plain-English Updates",
            "Works With Your Tools", "Honest Recommendations", "Talk to the Builder"]
snippet = ("Services", ["AI Agents", "Workflow Automation", "Lead Follow-Up", "CRM Automation",
                        "Zapier & Make", "Reporting"])
ok &= check("sitelink text", [s[0] for s in sitelinks], 25)
ok &= check("sitelink desc", [x for s in sitelinks for x in s[1:]], 35)
ok &= check("callouts", callouts, 25)
ok &= check("snippets", snippet[1], 25)
if not ok:
    sys.exit(1)
print("all limits OK")
