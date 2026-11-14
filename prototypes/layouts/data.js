// Prototype data. Headlines, leads, times and links are real items from public RSS feeds fetched on
// 30 Oct 2026 (The Hindu, Mint, The Indian Express, BBC, The Guardian, Al Jazeera).
// Reliability bands, extra outlet counts and source-card signals are ILLUSTRATIVE ONLY, so the layout
// can be judged at full density. The briefings were written by hand from the leads alone, following
// the briefing prompt's rules; in the app they are produced by the AI.
window.NA_DATA = (() => {
  const A = {
    // India
    a1: { src: "thehindu.com", title: "INDIA bloc's 'public impeachment' of CEC postponed to Oct. 31 due to rain, Rahul Gandhi says", url: "https://www.thehindu.com/news/national/cec-row-india-bloc-sir-protest-delhi-gyanesh-kumar-october-30-2026-live-updates/article71644149.ece", t: "2026-10-30T14:59:51+05:30" },
    a2: { src: "livemint.com", title: "INDIA bloc postpones 'public impeachment' of CEC Gyanesh Kumar due to rain; to hold meeting on Saturday", url: "https://www.livemint.com/news/india/india-bloc-postpones-public-impeachment-of-cec-gyanesh-kumar-due-to-rain-to-hold-meeting-on-saturday-11793352222575.html", t: "2026-10-30T15:04:09+05:30" },
    a3: { src: "livemint.com", title: "GSLV-F18/NVS-03 Mission launch today: Check time, how to watch LIVE", url: "https://www.livemint.com/news/india/gslvf18nvs03-mission-launch-today-check-time-how-to-watch-live-all-about-2nd-gen-navic-series-20th-gslv-flight-11793340303371.html", t: "2026-10-30T12:22:12+05:30" },
    a4: { src: "thehindu.com", title: "Ex-DMK Minister Anbil Mahesh Poyyamozhi arrested in private schools fraud case", url: "https://www.thehindu.com/news/national/tamil-nadu/former-dmk-minister-anbil-mahesh-poyyamozhi-arrested/article71626605.ece", t: "2026-10-30T12:21:55+05:30" },
    a5: { src: "thehindu.com", title: "Former MLA Muthukumar surrenders in bribery case involving ex-Minister Anbil Mahesh Poyyamozhi", url: "https://www.thehindu.com/news/cities/chennai/former-mla-muthukumar-surrenders-in-bribery-case-involving-ex-minister-anbil-mahesh-poyyamozhi/article71639781.ece", t: "2026-10-30T12:39:29+05:30" },
    a6: { src: "thehindu.com", title: "Police barged into my office to arrest former Minister, alleges senior advocate P. Wilson in Madras HC", url: "https://www.thehindu.com/news/national/tamil-nadu/police-barged-into-my-office-to-arrest-former-minister-anbil-mahesh-poyyamozhi-alleges-senior-advocate-p-wilson-in-madras-hc/article71641679.ece", t: "2026-10-30T03:00:20+05:30" },
    a7: { src: "indianexpress.com", title: "From Tata row to IDBI Bank sale: Rahul Gandhi vs Nirmala Sitharaman round 2", url: "https://indianexpress.com/article/india/from-tata-row-to-idbi-bank-sale-rahul-gandhi-vs-nirmala-sitharaman-round-2-10943189/", t: "2026-10-30T07:13:33+00:00" },
    a8: { src: "thehindu.com", title: "What does the shooting of a Kuno cheetah cub mean for Project Cheetah? | Explained", url: "https://www.thehindu.com/news/national/madhya-pradesh/cheetah-cub-shooting-mp-kuno-national-park-probe-explained/article71644165.ece", t: "2026-10-30T14:59:49+05:30" },
    a9: { src: "livemint.com", title: "Mint explainer: why India wants to make its own shipping containers", url: "https://www.livemint.com/news/container-manufacturing-shipping-containers-domestic-manufacturing-container-industry-maritime-logistics-supply-chain-11793328057166.html", t: "2026-10-30T13:33:13+05:30" },
    a10: { src: "indianexpress.com", title: "Mumbai-Ahmedabad bullet train project: How will India's first high-speed train get uninterrupted electricity?", url: "https://indianexpress.com/article/india/mumbai-ahmedabad-bullet-train-power-supply-14-traction-substations-10943302/", t: "2026-10-30T08:52:05+00:00" },
    a11: { src: "indianexpress.com", title: "Karnataka BJP MP Lahar Siroya slams Kharge for 'abusive' remarks against PM", url: "https://indianexpress.com/article/india/karnataka-bjp-mp-lahar-siroya-slams-kharge-for-abusive-remarks-against-pm-10943191/", t: "2026-10-30T07:21:37+00:00" },
    a12: { src: "thehindu.com", title: "Tamil Nadu Governor Arlekar, CM Vijay pay tribute to freedom fighter Pasumpon Muthuramalinga Thevar", url: "https://www.thehindu.com/news/national/tamil-nadu/tamil-nadu-governor-arlekar-cm-vijay-pay-tribute-to-freedom-fighter-pasumpon-muthuramalinga-thevar/article71643802.ece", t: "2026-10-30T14:59:39+05:30" },
    a13: { src: "thehindu.com", title: "Retd. IAS officer S. Natarajan is Tamil Nadu Chief Information Commissioner", url: "https://www.thehindu.com/news/national/tamil-nadu/retd-ias-officer-s-natarajan-is-tamil-nadu-chief-information-commissioner/article71644276.ece", t: "2026-10-30T14:40:41+05:30" },
    // Global
    b1: { src: "bbc.com", title: "Crypto giant Binance hit by $6.3bn hack suspected to be largest in history", url: "https://www.bbc.co.uk/news/articles/cwg7xp8kx4v5o", t: "2026-10-30T09:33:51+00:00" },
    b2: { src: "livemint.com", title: "Crypto exchange Binance freezes assets after $6.3 billion security breach", url: "https://www.livemint.com/news/crypto-exchange-binance-freezes-assets-after-6-3-billion-security-breach-11793335066186.html", t: "2026-10-30T10:16:56+05:30" },
    b3: { src: "bbc.com", title: "Google value plunges by $500bn as AI search hits ad sales", url: "https://www.bbc.co.uk/news/articles/cm9qzdqpxy48o", t: "2026-10-29T22:17:52+00:00" },
    b4: { src: "aljazeera.com", title: "Russian oil refinery struck as Ukraine renews drone attacks", url: "https://www.aljazeera.com/video/newsfeed/2026/10/30/russian-oil-refinery-struck-as-ukraine-renews-drone-attacks", t: "2026-10-30T09:14:16+00:00" },
    b5: { src: "theguardian.com", title: "Africa's 'unprecedented' Ebola outbreak could reach UK, says top health official", url: "https://www.theguardian.com/global-development/2026/oct/28/africa-ebola-outbreak-could-reach-uk-says-top-health-official", t: "2026-10-28T09:37:49+00:00" },
    b6: { src: "bbc.com", title: "Record number of purges as Xi emphasises 'party discipline' at China political gathering", url: "https://www.bbc.co.uk/news/articles/cq5ynw94k7kzo", t: "2026-10-30T03:24:12+00:00" },
    b7: { src: "aljazeera.com", title: "Smoke from wildfires killed 3,500 people in Indonesia in September: Report", url: "https://www.aljazeera.com/news/2026/10/30/smoke-from-wildfires-killed-3500-people-in-indonesia-in-september-report", t: "2026-10-30T08:20:30+00:00" },
    b8: { src: "theguardian.com", title: "Egypt fines prominent editor Lina Attalah and shuts independent news outlet Mada Masr", url: "https://www.theguardian.com/world/2026/oct/28/egypt-lina-attalah-mada-masr-press-freedom-clampdown", t: "2026-10-28T14:16:24+00:00" },
    // Chennai
    c1: { src: "thehindu.com", title: "Chennai's Sai Baba temple dispute: financial irregularities, unauthorised demolition flagged in interim report", url: "https://www.thehindu.com/news/cities/chennai/", t: "2026-10-30T12:41:19+05:30" },
    c2: { src: "thehindu.com", title: "Tamil Nadu Biodiversity Board to work on action plan for conservation of critically endangered species", url: "https://www.thehindu.com/news/cities/chennai/", t: "2026-10-30T15:03:54+05:30" },
  };

  const SOURCES = {
    "thehindu.com": { name: "The Hindu", band: "reliable", type: "News outlet", country: "India", owner: "The Hindu Group (private, family-owned)" },
    "livemint.com": { name: "Mint", band: "reliable", type: "News outlet", country: "India", owner: "HT Media (private, listed)" },
    "indianexpress.com": { name: "The Indian Express", band: "reliable", type: "News outlet", country: "India", owner: "Indian Express Group (private)" },
    "bbc.com": { name: "BBC News", band: "high", type: "Public broadcaster", country: "United Kingdom", owner: "BBC (public service, licence-fee funded)" },
    "theguardian.com": { name: "The Guardian", band: "high", type: "News outlet", country: "United Kingdom", owner: "Scott Trust (non-profit trust)" },
    "aljazeera.com": { name: "Al Jazeera", band: "reliable", type: "Broadcaster, state-funded", country: "Qatar", owner: "Al Jazeera Media Network (funded by the Government of Qatar)" },
  };

  // stories: arts = article ids (cluster), more = illustrative count of further outlets in the cluster
  const STORIES = {
    india: [
      { id: "cec", topic: "Politics", arts: ["a1", "a2"], more: 21, developing: true, lead: "The INDIA bloc moved its symbolic \u201cpublic impeachment\u201d of Chief Election Commissioner Gyanesh Kumar to 31 October because of rain in Delhi." },
      { id: "anbil", topic: "Crime and justice", place: "Tamil Nadu", arts: ["a4", "a5", "a6"], more: 14, lead: "Chennai police arrested Tamil Nadu\u2019s former School Education Minister after his anticipatory bail plea was rejected." },
      { id: "gslv", topic: "Science", arts: ["a3"], more: 17, lead: "ISRO is set to launch GSLV-F18 carrying the NavIC satellite NVS-03 at 11:51 pm on 30 October." },
      { id: "idbi", topic: "Politics", arts: ["a7"], more: 9, lead: "Rahul Gandhi and Nirmala Sitharaman traded charges over the Tata row and the IDBI Bank sale." },
      { id: "cheetah", topic: "Environment", arts: ["a8"], more: 11, kind: "Explainer", lead: "A 19-month-old cheetah cub was shot dead outside Kuno National Park, the first such case since Project Cheetah began in 2022." },
      { id: "containers", topic: "Business", arts: ["a9"], more: 4, kind: "Explainer", lead: "India plans to raise annual container-making capacity more than tenfold by FY40 through a \u20b910,000-crore scheme." },
      { id: "bullet", topic: "Business", arts: ["a10"], more: 3, kind: "Explainer", lead: "How the Mumbai\u2013Ahmedabad high-speed line will draw uninterrupted power." },
      { id: "kharge", topic: "Politics", arts: ["a11"], more: 6, lead: "Karnataka BJP MP Lahar Siroya criticised Kharge over what he called \u201cabusive\u201d remarks against the Prime Minister." },
      { id: "thevar", topic: "Politics", place: "Tamil Nadu", arts: ["a12"], more: 5, lead: "Governor Arlekar and Chief Minister Vijay paid tribute to Pasumpon Muthuramalinga Thevar." },
      { id: "tnic", topic: "Politics", place: "Tamil Nadu", arts: ["a13"], more: 2, lead: "Retired IAS officer S. Natarajan was appointed Tamil Nadu Chief Information Commissioner." },
    ],
    global: [
      { id: "binance", topic: "Technology", arts: ["b1", "b2"], more: 38, breaking: true, lead: "Binance reported a breach estimated at $6.3bn; two analytics firms attributed it to the North Korean unit TraderTraitor." },
      { id: "google", topic: "Business", arts: ["b3"], more: 29, lead: "Google\u2019s market value fell by about $500bn after it said AI search summaries were affecting ad revenue." },
      { id: "ukraine", topic: "Conflict", arts: ["b4"], more: 24, developing: true, lead: "Ukrainian drones hit a Russian oil refinery; Russian authorities say they intercepted more than 1,000 drones overnight." },
      { id: "xi", topic: "Politics", arts: ["b6"], more: 15, lead: "A record number of officials were purged as Xi Jinping stressed party discipline at a political gathering." },
      { id: "ebola", topic: "Health", arts: ["b5"], more: 12, lead: "The Africa CDC chief warned the outbreak could spread further, including to the UK, without more preparedness funding." },
      { id: "indonesia", topic: "Environment", arts: ["b7"], more: 8, lead: "Smoke from drought-driven wildfires killed 3,500 people in Indonesia in September, a report says." },
      { id: "mada", topic: "Politics", arts: ["b8"], more: 7, lead: "A Cairo court convicted editor Lina Attalah of running an unlicensed website; Mada Masr was shut." },
    ],
    chennai: [
      { id: "anbil", topic: "Crime and justice", arts: ["a4", "a5", "a6"], more: 14, lead: "Chennai police arrested Tamil Nadu\u2019s former School Education Minister after his anticipatory bail plea was rejected." },
      { id: "saibaba", topic: "Society", arts: ["c1"], more: 3, lead: "An interim report flagged financial irregularities and an unauthorised demolition in the Sai Baba temple dispute." },
      { id: "biodiv", topic: "Environment", arts: ["c2"], more: 2, lead: "The Tamil Nadu Biodiversity Board will draw up an action plan for critically endangered species." },
      { id: "thevar", topic: "Politics", arts: ["a12"], more: 5, lead: "Governor Arlekar and Chief Minister Vijay paid tribute to Pasumpon Muthuramalinga Thevar." },
    ],
  };

  const TITLES = {
    cec: "INDIA bloc postpones \u2018public impeachment\u2019 of Chief Election Commissioner to 31 October",
    anbil: "Former Tamil Nadu minister Anbil Mahesh Poyyamozhi arrested in private schools fraud case",
    gslv: "ISRO to launch NavIC satellite NVS-03 on GSLV-F18 tonight",
    idbi: "Rahul Gandhi and Nirmala Sitharaman spar over Tata row and IDBI Bank sale",
    cheetah: "Cheetah cub shot dead outside Kuno, first such case since Project Cheetah began",
    containers: "Why India wants to make its own shipping containers",
    bullet: "How the Mumbai\u2013Ahmedabad bullet train will get uninterrupted power",
    kharge: "Karnataka BJP MP criticises Kharge over remarks about the Prime Minister",
    thevar: "Governor and Chief Minister pay tribute to Muthuramalinga Thevar",
    tnic: "S. Natarajan named Tamil Nadu Chief Information Commissioner",
    binance: "Binance hit by $6.3bn hack, suspected to be the largest crypto theft on record",
    google: "Google loses about $500bn in value as AI search weighs on ad sales",
    ukraine: "Ukraine strikes Russian oil refinery in renewed drone attacks",
    xi: "Record number of purges as Xi stresses party discipline",
    ebola: "Africa CDC warns Ebola outbreak could spread beyond the continent",
    indonesia: "Wildfire smoke killed 3,500 people in Indonesia in September, report says",
    mada: "Egypt convicts editor Lina Attalah and shuts Mada Masr",
    saibaba: "Interim report flags irregularities in Chennai Sai Baba temple dispute",
    biodiv: "Tamil Nadu Biodiversity Board to plan for critically endangered species",
  };

  const BRIEFINGS = {
    india: {
      title: "India, last 24 hours",
      big: [{ t: "Politics leads: the opposition INDIA bloc\u2019s campaign against the Chief Election Commissioner was delayed by rain, and Rahul Gandhi and Nirmala Sitharaman clashed again over the IDBI Bank sale.", c: ["a1", "a2", "a7"] },
            { t: "In Tamil Nadu, a former School Education Minister was arrested in a private schools fraud case. ISRO\u2019s NavIC launch is due tonight.", c: ["a4", "a3"] }],
      stories: [
        { id: "cec", h: TITLES.cec, t: "The bloc moved its symbolic \u201cpublic impeachment\u201d of CEC Gyanesh Kumar to 31 October because of rain in Delhi. The event is part of its campaign over alleged \u201cvote chori\u201d and the SIR exercise. Rahul Gandhi said the Home Minister was \u201ctrying his best\u201d to stop people from other states joining a 1 November rally.", c: ["a2", "a1"], differ: "" },
        { id: "anbil", h: TITLES.anbil, t: "Chennai police took Anbil Mahesh Poyyamozhi into custody after his anticipatory bail plea was rejected; he is remanded until 6 November. Former MLA Muthukumar surrendered after the Supreme Court dismissed his own bail petition.", c: ["a4", "a5"], differ: "Senior advocate P. Wilson told the Madras High Court that police entered his office to make the arrest; the State Public Prosecutor denies this.", dc: ["a6"] },
        { id: "gslv", h: TITLES.gslv, t: "GSLV-F18 is scheduled to lift off at 11:51 pm with NVS-03, a second-generation NavIC navigation satellite. It is the 20th GSLV flight.", c: ["a3"], differ: "" },
        { id: "idbi", h: TITLES.idbi, t: "Rahul Gandhi and Nirmala Sitharaman exchanged charges over the Tata row and the sale of IDBI Bank, a second round of their dispute.", c: ["a7"], differ: "" },
        { id: "cheetah", h: TITLES.cheetah, t: "A 19-month-old male cub, KNG22, died on 27 October after being shot outside Kuno National Park. The Centre and Madhya Pradesh have set up separate inquiries.", c: ["a8"], differ: "" },
      ],
      watch: [{ t: "GSLV-F18 launch at 11:51 pm tonight.", c: ["a3"] }, { t: "INDIA bloc event on 31 October and rally in Delhi on 1 November.", c: ["a1", "a2"] }],
      unknown: ["What the separate cheetah inquiries have found so far."],
    },
    global: {
      title: "World, last 24 hours",
      big: [{ t: "A breach at Binance estimated at $6.3bn, which analysts attribute to North Korean hackers, and a $500bn fall in Google\u2019s value lead technology and markets.", c: ["b1", "b2", "b3"] },
            { t: "Ukraine renewed drone attacks on Russian energy sites, and China\u2019s leadership marked a record number of purges.", c: ["b4", "b6"] }],
      stories: [
        { id: "binance", h: TITLES.binance, t: "Binance reported a breach with an estimated $6.3bn impact. About $3bn has been located, and the exchange says it will cover losses. Two analytics firms attributed the attack to TraderTraitor, a North Korean hacking unit.", c: ["b2", "b1"], differ: "" },
        { id: "google", h: TITLES.google, t: "Google\u2019s shares hit a six-month low after it said its AI search summaries were affecting ad revenue.", c: ["b3"], differ: "" },
        { id: "ukraine", h: TITLES.ukraine, t: "A Russian oil refinery was struck. Russian authorities say they intercepted more than 1,000 Ukrainian drones over several regions overnight.", c: ["b4"], differ: "" },
        { id: "xi", h: TITLES.xi, t: "Observers quoted by the BBC say Xi\u2019s anti-corruption campaign has become an effort to consolidate authority.", c: ["b6"], differ: "" },
      ],
      watch: [{ t: "Whether Binance recovers the remaining funds.", c: ["b2"] }],
      unknown: ["How the Binance breach was carried out."],
    },
    chennai: {
      title: "Chennai, last 24 hours",
      big: [{ t: "The arrest of former minister Anbil Mahesh Poyyamozhi dominates Chennai news, alongside an interim report on the Sai Baba temple dispute.", c: ["a4", "c1"] }],
      stories: [
        { id: "anbil", h: TITLES.anbil, t: "Police took the former minister into custody after his anticipatory bail plea was rejected; he is remanded until 6 November.", c: ["a4"], differ: "His counsel says police entered a lawyer\u2019s office for the arrest; the prosecution denies it.", dc: ["a6"] },
        { id: "saibaba", h: TITLES.saibaba, t: "An interim report flagged financial irregularities and an unauthorised demolition.", c: ["c1"], differ: "" },
      ],
      watch: [{ t: "Remand ends on 6 November.", c: ["a4"] }],
      unknown: [],
    },
  };

  const BRIEFS = {
    anbil: {
      kind: "News",
      summary: "Chennai police arrested former Tamil Nadu School Education Minister Anbil Mahesh Poyyamozhi in a private schools fraud case after his anticipatory bail plea was rejected.",
      points: [
        { t: "He was remanded to judicial custody until 6 November.", c: ["a4"] },
        { t: "Former MLA Muthukumar, also named in the case, surrendered on Thursday after the Supreme Court dismissed his anticipatory bail petition on Wednesday.", c: ["a5"] },
        { t: "Senior advocate P. Wilson told the Madras High Court that police \u201cbarged into\u201d his office to make the arrest.", c: ["a6"] },
        { t: "State Public Prosecutor R. John Sathyan denied this, saying police were invited in and did not arrest the accused inside the office.", c: ["a6"] },
      ],
      open: ["The specific charges and the amount involved are not stated in these reports."],
    },
  };

  return { A, SOURCES, STORIES, TITLES, BRIEFINGS, BRIEFS };
})();
