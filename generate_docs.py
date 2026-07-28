"""Generate Google Ads API application documents."""
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import os

OUT_DIR = os.path.expanduser("~/Desktop")

# === DOC 1: API Application Answers ===
doc = Document()
style = doc.styles['Normal']
font = style.font
font.name = 'Calibri'
font.size = Pt(11)

doc.add_heading('Google Ads API Application — Supporting Information', 0)
doc.add_paragraph('Applicant: Dreame Technology Co., Ltd. (追觅科技)')
doc.add_paragraph('Date: July 2026')

doc.add_heading('6. Business Model & Google Ads Usage', level=1)

doc.add_heading('Company Overview', level=2)
doc.add_paragraph(
    'Dreame Technology Co., Ltd. (追觅科技) is a mobile game developer and publisher headquartered in China. '
    'Our core business model is developing and publishing free-to-play (F2P) mobile games monetized through '
    'in-app purchases (IAP). We self-publish our games in key Asian markets including Japan, Taiwan, and Hong Kong.'
)

doc.add_heading('Active Game Portfolio', level=2)
doc.add_paragraph(
    'Our active titles promoted via Google Ads include:'
)
games = [
    ('[需确认: 心动防线 Google Play listing name]', 'An anime-style bounce-battle RPG (弹射RPG) for the Japanese market, available on Android and iOS.'),
    ('[需确认: 燃烧纪元 Google Play listing name]', 'For the Taiwan/Hong Kong market.'),
    ('[需确认: 心动繁体 Google Play listing name]', 'Traditional Chinese version for the Taiwan market.'),
]
for name, desc in games:
    p = doc.add_paragraph(style='List Bullet')
    p.add_run(name).bold = True
    p.add_run(f' — {desc}')

doc.add_heading('How We Use Google Ads', level=2)
doc.add_paragraph(
    'Google Ads (specifically App Campaigns, formerly Universal App Campaigns) is our primary paid user '
    'acquisition channel. We run multiple concurrent campaigns across Google Search, YouTube, the Display '
    'Network, and Google Play Store listings. Our campaigns target Japanese and Traditional Chinese-speaking '
    'RPG players segmented by device platform (Android / iOS).'
)
doc.add_paragraph(
    'Our approximate monthly Google Ads spend is [需确认: $XX,XXX USD]. We currently manage approximately '
    '[需确认: XX] active App Campaigns and launch [需确认: XX] new ad creatives per week.'
)

doc.add_heading('Why We Need the Google Ads API', level=2)
doc.add_paragraph(
    'We are requesting API access to solve a specific operational bottleneck in our creative analytics pipeline. '
    'Currently, our team manually exports Google Ads performance reports to CSV weekly, then imports them into '
    'our internal analytics system (MOVA) to correlate creative performance with post-install user behavior data '
    'from our Mobile Measurement Partner (AppsFlyer). This manual process is error-prone and limits our ability '
    'to optimize creatives in near real-time.'
)
doc.add_paragraph('Specifically, the API will enable us to:')
reasons = [
    'Retrieve asset-level performance data (individual images, videos, and headlines within responsive ads) '
    '— this granularity is not available through AppsFlyer or the Google Ads web interface.',
    'Automate daily creative performance reporting, replacing the current weekly manual CSV export workflow '
    'and saving an estimated 4–6 hours of staff time per week.',
    'Correlate Google Ads creative asset data with post-install metrics (D1/D7 ROI, retention, LTV) from '
    'AppsFlyer through our internal MOVA analytics pipeline, enabling data-driven creative production decisions.',
    'Identify which specific creative elements (hook types, character designs, visual styles) drive the '
    'highest return on ad spend in each target market.',
]
for r in reasons:
    doc.add_paragraph(r, style='List Number')

doc.add_heading('Data Usage & Compliance', level=2)
doc.add_paragraph(
    'All data retrieved via the Google Ads API will be used exclusively for internal reporting, creative '
    'optimization, and budget allocation decisions. Data access will be restricted to our UA (User Acquisition) '
    'team and analytics engineers via an internal dashboard with role-based access control. '
    'No Google Ads data will be resold, redistributed to third parties, or exposed in any client-facing or '
    'public-facing product. We comply with all Google Ads API Terms of Service and data protection requirements.'
)

doc.add_heading('8. Access Scope', level=1)
doc.add_paragraph('Internal users employees only (outsourcing, contractor included).')
doc.add_paragraph(
    'The API token will be used exclusively by employees and authorized contractors of Dreame Technology. '
    'Access is limited to the UA team (approximately [需确认: X] people) and analytics engineers who maintain '
    'the MOVA internal reporting system. No external users, clients, or general public will have access.'
)

doc.add_heading('9. Third-Party Tool Usage', level=1)
doc.add_paragraph('No.')
doc.add_paragraph(
    'Our Google Ads API integration is developed entirely in-house using the Google Ads Python client library. '
    'We do not use any third-party tools, SaaS platforms, or external services that consume Google Ads API tokens. '
    'The integration code runs on our own secure Windows server within our corporate network.'
)

out1 = os.path.join(OUT_DIR, "Google_Ads_API_Application_Dreame.docx")
doc.save(out1)
print(f"Saved: {out1}")

# === DOC 2: MOVA System Design Document ===
doc2 = Document()
style2 = doc2.styles['Normal']
style2.font.name = 'Calibri'
style2.font.size = Pt(10)

doc2.add_heading('MOVA Creative Analytics System — Design Document', 0)
doc2.add_paragraph('Dreame Technology Co., Ltd. | Internal Use Only | July 2026')

doc2.add_heading('1. System Overview', level=1)
doc2.add_paragraph(
    'MOVA is an internal creative analytics system designed to help our User Acquisition team understand '
    'which ad creatives drive the best post-install user behavior in each target market. The system integrates '
    'three data sources — Google Ads API (creative asset data), AppsFlyer (post-install metrics), and our '
    'internal creative asset library (Alibaba Cloud OSS) — to produce a unified view of creative performance.'
)

doc2.add_heading('2. System Architecture', level=1)
doc2.add_paragraph(
    'MOVA consists of the following components:'
)
components = [
    ('Data Ingestion Layer',
     'Google Ads API → daily pull of asset-level performance data (impressions, clicks, spend, installs '
     'per creative asset). AppsFlyer API → post-install metrics (D1/D7 ROI, retention, LTV). Both data '
     'streams are normalized and stored in a local SQLite database.'),
    ('AI Creative Deconstruction Engine',
     'Downloaded ad creatives (video/image) are analyzed by a local AI pipeline: CLIP (visual semantics), '
     'PaddleOCR (text recognition), faster-whisper (audio transcription). Analysis results are structured '
     'into a standardized tag taxonomy covering 6 dimensions: hook strategy, character elements, scene '
     'elements, production method, narrative mode, and visual style.'),
    ('Performance Attribution Engine',
     'Creative tags are joined with Google Ads cost data and AppsFlyer LTV data through a mapping table. '
     'A regression model identifies which creative elements correlate with high ROI.'),
    ('Reporting & Dashboard',
     'Results are published to Feishu Base (multidimensional tables) for team access, and a Feishu Bot '
     'pushes daily summary reports to the UA team chat group.'),
]
for title, desc in components:
    doc2.add_heading(title, level=2)
    doc2.add_paragraph(desc)

doc2.add_heading('3. Data Flow', level=1)
doc2.add_paragraph(
    '1. Google Ads API → Python client → daily pull of AdGroupAd and AdGroupAdAssetView → local SQLite\n'
    '2. AppsFlyer API → Python client → raw install/event reports → normalized attribution\n'
    '3. OSS creative library → download → AI deconstruction (CLIP + OCR + ASR) → tag taxonomy\n'
    '4. JOIN: creative tags + Google Ads asset data + AppsFlyer LTV data\n'
    '5. Regression analysis → cluster by performance → generate insights\n'
    '6. Push results to Feishu Base (internal BI dashboard) + Feishu Bot daily summary\n'
    '7. All processing is local (Windows Server within corporate network), no external data transmission'
)

doc2.add_heading('4. Technology Stack', level=1)
doc2.add_paragraph(
    'Language: Python 3.14\n'
    'Database: SQLite (local, single-machine)\n'
    'Google Ads Integration: google-ads Python client library (API v18)\n'
    'MMP Integration: AppsFlyer API V2 (Bearer token, read-only)\n'
    'AI/CV: HuggingFace CLIP (ViT-B/32), PaddleOCR, faster-whisper (all running locally)\n'
    'LLM: Anthropic Claude (for creative analysis summarization)\n'
    'Cloud Storage: Alibaba Cloud OSS (creative assets)\n'
    'Collaboration: Feishu Base + Feishu Bot (internal only)\n'
    'Server: Windows 11 Pro, local network, no public internet exposure for the analysis pipeline'
)

doc2.add_heading('5. Security & Access Control', level=1)
doc2.add_paragraph(
    '- Google Ads API token stored in encrypted local environment file, never committed to code repository\n'
    '- All API calls are read-only (no campaign management, bidding, or ad modification)\n'
    '- Analysis pipeline runs on a local Windows server within corporate firewall\n'
    '- No external network exposure — data only flows inbound from Google Ads/AppsFlyer APIs\n'
    '- Internal access control via Feishu Base permissions (role-based)\n'
    '- API refresh token rotation managed per Google security best practices'
)

doc2.add_heading('6. API Usage Scope', level=1)
doc2.add_paragraph(
    'We request access to the following Google Ads API services (read-only):\n'
    '- GoogleAdsService.search / GoogleAdsService.searchStream — for querying campaign, ad group, '
    'ad group ad, and asset-level performance metrics\n'
    '- Specific resources: customer, campaign, ad_group, ad_group_ad, asset, '
    'ad_group_ad_asset_view, click_view\n'
    'We do NOT require write access to campaigns, ads, or bidding. We will not use the API '
    'for automated campaign management, bid optimization, or ad creation.'
)

doc2.add_heading('7. Compliance', level=1)
doc2.add_paragraph(
    'We confirm that:\n'
    '- Our use of the Google Ads API complies with the Google Ads API Terms of Service\n'
    '- We will not exceed the requested access level or rate limits\n'
    '- We maintain a valid Google Ads Manager Account in good standing\n'
    '- We will promptly report any security incidents related to API usage\n'
    '- Our developer token will not be shared, transferred, or used by any third party'
)

out2 = os.path.join(OUT_DIR, "MOVA_System_Design_Document.docx")
doc2.save(out2)
print(f"Saved: {out2}")
print("Done!")
