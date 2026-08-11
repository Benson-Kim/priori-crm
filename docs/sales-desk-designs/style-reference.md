# Sales Desk — Frontend Style Reference

Extracted verbatim from the text-preserving Figma SVG exports in `docs/sales-desk-designs/` (branch `sales-desk-designs`). All 13 screens screened in full. This is the single source of truth for the frontend agent building #46–#50.

> **Branding conflict:** designs still show "Priori / Priori Technologies / © 2026 Priori". Per !37 the product name is **Business Central** — always substitute.

---

## 1. Design tokens

### Colors
| Token | Hex | Usage |
|---|---|---|
| `brand` | `#912B90` | Primary buttons, active nav, avatars, badges, selected tabs, stage-progress fill, focus/inner-shadow accents |
| `brand-bg` | `#FBF0FB` | Active nav pill, brand chips ("1 total", stage "Proposal & Quote"), filter-tab background |
| `ink` | `#16233A` | Primary text, dark buttons/toggle-active (`Monthly` selected, team card bg) |
| `muted` | `#6B7688` | Secondary text, icons, column sub-labels |
| `faint` | `#9CA3AF` | Placeholders, disabled counts, unsynced dot/label, chevrons |
| `border` | `#E4E8EF` | 1px hairlines, table row dividers, input borders, progress track |
| `surface` | `#F6F7F9` | App background, table header rows, stat chips |
| `card` | `#FFFFFF` | Cards, panels, drawers |
| `info` / `info-bg` | `#2456E6` / `#EBF0FF` | Currency chips (USD/KES), "1 open" chips, KPI weighted value, Activation stage |
| `success` / `success-bg` | `#0F7A38` / `#E6F5EB` | Won/Negotiation chips, "Synced", "In accounting" dot |
| `success-alt` | `#16A34A`, `#15803D`/`#DCFCE7` (fresh "1d in pipeline"), `#22C55E` (tab status dot) | Won bars, progress end-cap |
| `warn` / `warn-bg` | `#B45309` / `#FBF1DE` | Qualification/Pending chips, "Not synced" dot, "Today" badge |
| `warn-alt` | `#854D0E` / `#FEF9C3` | "1 cold", "52d in pipeline" aging chips |
| `danger` / `danger-bg` | `#C23434` / `#FBEBEB` | Lost chips, "Overdue", stale "Last activity 38d ago"; `#EF4444` lost progress cap |
| `progress` | `#1D4ED8` current-stage segment, `#93C5FD` completed segments, `#E4E8EF` upcoming |
| Avatar palette | `#2456E6` (TK), `#7C3AED` (MW), `#0D9488` (JN) | Deterministic per-user |
| Logo gradient | `#912B90 → #2456E6` (45°) | App mark |

### Typography (Poppins; Menlo for codes)
| Style | Spec |
|---|---|
| Page title | Poppins Bold 24 |
| Breadcrumb/topbar title | Poppins 600 13 |
| Section/card heading | Poppins 600 13–14 |
| KPI value | Poppins Bold 25.6 |
| KPI label | Poppins 600 12, letter-spacing 0.3px, UPPERCASE |
| Section micro-label | Poppins Bold 10, letter-spacing 1px, UPPERCASE (e.g. `BILLING PROFILES`, `STAGE RECORD`, `RECENT QUOTES`) |
| Body/cell | Poppins 13 (primary `ink`, secondary `muted`) |
| Sub-cell / meta | Poppins 12 / 11 / 10 |
| Chips/badges | Poppins 600 12 (10 bold for counters) |
| Codes (profile/quote IDs, tenant domains) | **Menlo 12–13**, bold for IDs (`Q-2031` in brand color, `BL-USD` in ink/muted) |
| Avatar initials | Poppins Bold 10–13, white |

### Shape & effects
- Radius: cards/panels 16; buttons/inputs/chips 10–12; pills/badges fully rounded; table container 16 with 1px `border`.
- Card shadow: `0 1px 3px rgba(0,0,0,0.06)`; drawer shadow: `-8px 0 32px rgba(0,0,0,0.10)`.
- Selected row highlight: `#FBF0FB` bg + 3px `brand` inner-left border.
- Layout: 240px fixed sidebar (white, 1px right border), 64px topbar, content pad ~24px, page canvas 1551×1024.

---

## 2. Shared chrome (every page)
- **Sidebar**: logo block (mark + product name + subtitle), nav: Dashboard, Pipeline, Companies, Future pipeline, Quotes & pricing, Onboarding. Active item = `brand-bg` pill + `brand` icon/text. Nav count badges: 16px `brand` circle, white bold 10 (Companies 2, Future pipeline 2). Footer: user card (36px avatar, name 600 12, role 10) + copyright + `Version: 1.0.188-288`.
- **Topbar**: page title (600 13) · global search pill "Search companies, deals…" (`surface` bg, 1px border) · bell icon with `brand` count badge (e.g. 5) · 32px user avatar.

## 3. Components
- **KPI card**: white, label (uppercase 12/600/0.3px muted) → value (Bold 25.6, semantic color) → sub-line (12 muted).
- **Currency chip**: `info-bg` bg, `info` 600 12 text (USD/KES).
- **Status chips**: Synced (`success`), Pending (`warn`), Draft (gray `#F3F4F6`/`muted`); stage chips per color map; due badges Overdue (`danger`), Today (`warn`), `45d`/`85d` (gray).
- **Sync indicator**: 8px dot + label — green "In accounting" / gray-amber "Not synced".
- **Filter tabs**: pill group, active = `brand` filled white text (table view) or `brand-bg` outline (board view); counts in parentheses or superscript bold 10.
- **Segmented control**: `Monthly | Annual −15%` and `USD | KES | EUR | GBP` — active segment filled (`brand` or `ink`), 28px tall.
- **Progress**:
  - Onboarding: 6px rounded bar, `brand` fill + "N of 7 tasks complete" + % badge (`brand-bg`).
  - Deal stage: 5 segments (10px board / 6px drawer) — completed `#93C5FD`, current `#1D4ED8`, upcoming `border`; end-cap green `#16A34A` (won) or red `#EF4444` (lost). Label: `Stage N of 5 · <Stage>`.
  - Rep quota: 8px bar in rep avatar color + `$won / $quota` + bold % right-aligned.
- **Checklist row**: 20px rounded-4 checkbox — filled `brand` + white check + strikethrough muted label when done; gray `#C8CDD8` outline + ink label when pending; right-aligned `Step N`.
- **Tables**: header row `surface` bg, 600 13 ink labels; rows 43–78px, hairline dividers; row chevron `›` muted.
- **Buttons**: primary `brand` filled white 600 12–13 (`+ New Company`, `Save quote`, `Sync both profiles`, `Start engaging →`, `Advance to <stage> →`, `Re-sync USD profile`); outline-success `Close — Won`; outline-danger `Close — Lost`; secondary outline gray (`Log activity`, `Sync both`, `↓ Export CSV`); text-link `brand` (`+ Add line`, `Move to future pipeline →`).
- **Drawers** (right, 390px, white, left shadow): header + × close, micro-label sections, hairline dividers.

## 4. Screen inventory & key copy
| File | Screen |
|---|---|
| `Dashboard.svg` | Dashboard: 4 KPIs (PIPELINE (WEIGHTED) $46,530 · TOTAL ARR PIPELINE $72,180 "Unweighted" · WON THIS PERIOD $136,800 · LOST THIS PERIOD $5,250) · Bookings — 12 months (Won green / Lost red bars, Sep–Aug) · Active pipeline by stage (`$X · N deal`) · Rep pipeline vs. quota (7% / 22% / 2%) · Recently added companies |
| `Companies.svg` | Companies list: Company(+phone), Industry, Contact(+email), Tenant (Menlo, "—" empty), Currency chip, Owner, Deals (`1 total`/`1 open`); "6 companies"; `+ New Company` |
| `App__1_.svg` | Companies · billing profiles: tabs `All (6) | Needs sync (2)`; per company two rows `USD BL-USD ● In accounting` / `KES …`; Deals `1 open / 0 closed`; row action = `Synced` chip **or** `Sync both profiles` button |
| `Pipeline.svg` | Pipeline table: tabs All(6)/Open(4)/Won(1)/Lost(1); Company, Product, Seats, Value (ARR), Weighted (— when closed), Stage chip, Owner, › |
| `App.svg` | Pipeline board: rep cards (`2 open · $14.6k`, `50% won`, `1 cold`), "Whole team" dark card; stage columns w/ `1 · $11.9k` + `avg 21d in stage`; CLOSED column `2 · 50% won`; hygiene chips All deals 6 / Active this week 3 / 8–30 days quiet 0 / No activity 30d+ 1 / Open 45d+ 1; `Show closed`; "Search deals…" |
| `Define_design_guidelines.svg` | Pipeline **deal list + deal drawer**: list columns Deal / Time in pipeline (chip: green fresh, amber aging, gray closed "90d total") + "Last activity Nd ago" (red when stale) / Value / yr / Progress (5-seg + `Stage N of 5 · X`, `Won — Migration & support offer`, `Lost — Price too high`) / Latest record (note + `Stage · date`); toolbar `↓ Export CSV` + `Open pipeline $72.2k/yr`. **Deal drawer**: header (company, contact · product · seats, "Owned by X"), stat chips (`KSh 351k` "Annual value · KES", `1d` In pipeline, `1d` Since last activity), `Billing to NDG-KES · 14 days · VAT 16% · ● Not synced`, stage stepper (5), STAGE RECORD (dot, stage, date, note), LOG OR MOVE: textarea "What happened at Activation? A note is required to advance or close.", buttons `Log activity` / `Advance to Qualification →` / `Close — Won` / `Close — Lost`, link `Move to future pipeline →` |
| `Define_design_guidelines__1_.svg` | Companies + **company drawer**: header (name, `industry · registered 18 Jul 2026`, owner), CONTACT/EMAIL/PHONE/TENANT grid, BILLING PROFILES: tab toggle `USD profile | KES profile` (green status dots), code `BL-USD` + `Default` badge + "In accounting", selects Payment terms ("30 days"), Tax treatment ("VAT 0%"), input `Credit limit (USD)` ("2750000"), helper *"Editing a profile marks it out of sync until you push it again."*, buttons `Re-sync USD profile` + `Sync both`; DEALS: mini deal card (product, USD chip, `$12k/yr`, 5-seg progress, `Proposal & Quote · 21d in pipeline`) |
| `Future_Pipeline.svg` | Nurture cards: "Nurture list — 4 companies · est. $67,020 potential ARR"; card = company/contact, due badge, note, owner, Est. ARR, `Engage on YYYY-MM-DD` |
| `App__2_.svg` | Prospects table: "Planned prospects" `3` + `1 due now`; Company (status dot) / Contact / Owner / Engage on (chip `Today` or date) / Est. ARR / Note; row action `Start engaging →` |
| `Onboarding.svg` | Two checklist cards: `Acacia Insurance — Microsoft 365 E5 · 200 seats — 43% — 3 of 7`; `Highlands Coffee Exporters — Business Standard · 25 seats — 86% — 6 of 7`. Steps 1–7: Kick-off meeting, Tenant & domain setup, Licenses assigned, Data migration, Security baseline, User training, Handover to support |
| `Quotes___Pricing.svg` | Active quotes (Quote ID `Q-2031` Menlo brand, Company, Profile `BAR-USD`, Currency chip, **Total (USD eq.)**, Date, Status) + price list "Microsoft 365 — list prices (USD/seat/month)": Basic $6 / Standard $12.50 / Premium $22 / E3 $36 / E5 $57 / Exchange Online P1 $4 / Teams Phone $8 / Copilot $30 (Monthly/seat, Annual/seat, 10-seat ARR); `+ New Quote` |
| `App__3_.svg` / `App__4_.svg` | Quote builder: company select · currency segmented `USD | KES | EUR | GBP` · `Posts to BL-KES · 14 days · VAT 16% · ● Not synced` · lines (Product select, Seats, Billing `Monthly`/`Annual −15%`, Extra disc. %, Per user/mo w/ strikethrough list price, Line total `per month`/`per year`, ×) · `+ Add line` · `Subtotal KSh 166,278.00 · VAT 16% KSh 26,604.48` · **Total KSh 192,882.48** · `≈ $1,489.44 · €1,370.28 · £1,176.66` · `Save quote` · RECENT QUOTES rail (`Q-2033 · Pending · Kilifi Beach Resorts · KIL-USD · 10 Aug 2026 · $5,760`) |

## 5. Semantic rules (data ⇄ UI)
- **Currencies**: companies/billing profiles = USD & KES only; quote pricing currency = USD/KES/EUR/GBP with FX equivalents; lists show USD-equivalents; drawer shows native currency (`KSh 351k · Annual value · KES`).
- **Stages** (5, fixed order): Activation → Qualification → Proposal & Quote → Negotiation → Won (closed = Won/Lost). Advancing/closing **requires a note**.
- **Sync**: any profile edit ⇒ "Not synced"; actions `Re-sync <CCY> profile` / `Sync both (profiles)`; list filter "Needs sync (N)".
- **Hygiene buckets**: Active this week / 8–30 days quiet / No activity 30d+ / Open 45d+; per-deal chips `Nd in pipeline` (green ≤7d, amber >7d, gray closed "Nd total") + "Last activity Nd ago" (red ≥30d).
- **Codes**: illustrative slug+currency (`BL-USD`, `NDG-KES`, `SM-`, `KBR-`, `AI-`, `TS-`; also `BAR-`, `SAV-`, `KIL-` on Quotes page — inconsistency, backend uses slug+sequence). One profile per company is `Default`.
- Dates: `YYYY-MM-DD` in tables/logs, `10 Aug 2026` in cards/chips. Money: `$11,880` / `KSh 79,254.00` / compact `$14.6k`, `$72.2k/yr`.
