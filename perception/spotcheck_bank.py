"""Question bank for the observed spot-check: patient-language questions per specialty.

Each specialty entry gives the Spanish term for the specialist, and short lists of conditions,
procedures and symptoms written the way a patient would type them. build_question_set() turns
one entry plus the market into ~30 questions across nine categories (best in market, conditions,
procedures, symptoms, cost & insurance, access & urgency, about you by name, comparison & second
opinion, Spanish). Unknown specialties fall back to generic wording that still inserts the
specialty name. Hospitals use a service-line bank the same way.
"""
from __future__ import annotations

import os

MAX_QUESTIONS = int(os.environ.get("SPOTCHECK_MAX_QUERIES", "30"))

CATEGORIES = {
    "best": "Best in the market", "condition": "Conditions", "procedure": "Procedures", "symptom": "Symptoms",
    "cost": "Cost & insurance", "access": "Access & urgency", "direct": "About you, by name",
    "compare": "Comparison & second opinion", "spanish": "In Spanish",
}

# key: (match keywords, spanish specialist, conditions[(en, es)], procedures[(en, es)], symptoms[en])
_B = {
 "orthopedics": (["ortho"], "ortopedista",
   [("knee arthritis","artritis de rodilla"),("a torn rotator cuff","un desgarro del manguito rotador"),("hip pain that won't go away","dolor de cadera persistente"),("a torn ACL","una rotura del ligamento cruzado")],
   [("a knee replacement","un reemplazo de rodilla"),("a hip replacement","un reemplazo de cadera"),("shoulder arthroscopy","una artroscopia de hombro")],
   ["knee pain going up stairs","a shoulder that pops and hurts","numbness in my hand"]),
 "sports medicine": (["sports"], "médico deportivo",
   [("a torn ACL","una rotura del ligamento cruzado"),("a torn meniscus","un desgarro de menisco"),("tennis elbow","codo de tenista"),("a stress fracture","una fractura por estrés")],
   [("ACL reconstruction","una reconstrucción del ligamento cruzado"),("a PRP injection","una inyección de plasma rico en plaquetas"),("cartilage repair","una reparación de cartílago")],
   ["my knee gave out playing soccer","a hamstring that keeps pulling","shoulder pain when I throw"]),
 "spine": (["spine"], "especialista en columna",
   [("a herniated disc","una hernia de disco"),("sciatica","ciática"),("spinal stenosis","estenosis espinal"),("scoliosis","escoliosis")],
   [("a spinal fusion","una fusión espinal"),("a microdiscectomy","una microdiscectomía"),("an epidural steroid injection","una inyección epidural")],
   ["back pain shooting down my leg","neck pain with tingling in my arm","lower back pain for months"]),
 "hand": (["hand"], "cirujano de mano",
   [("carpal tunnel","túnel carpiano"),("trigger finger","dedo en gatillo"),("a wrist fracture","una fractura de muñeca")],
   [("carpal tunnel release","una cirugía de túnel carpiano"),("a wrist fracture repair","una reparación de fractura de muñeca")],
   ["numb fingers at night","a finger that locks"]),
 "podiatry": (["podiat","foot"], "podólogo",
   [("plantar fasciitis","fascitis plantar"),("bunions","juanetes"),("an ankle sprain that won't heal","un esguince de tobillo que no sana"),("a diabetic foot ulcer","una úlcera del pie diabético")],
   [("bunion surgery","una cirugía de juanetes"),("ankle replacement","un reemplazo de tobillo")],
   ["heel pain every morning","a toenail that's infected"]),
 "cardiology": (["cardio"], "cardiólogo",
   [("atrial fibrillation","fibrilación auricular"),("high blood pressure that isn't controlled","presión alta no controlada"),("heart failure","insuficiencia cardíaca"),("a heart valve problem","un problema de válvula cardíaca")],
   [("a cardiac catheterization","un cateterismo cardíaco"),("a stent","un stent"),("a cardiac ablation","una ablación cardíaca")],
   ["chest tightness when I walk","my heart racing at night","shortness of breath climbing stairs"]),
 "cardiothoracic": (["cardiothoracic","cardiac surgery","heart surgery"], "cirujano cardíaco",
   [("a blocked artery","una arteria bloqueada"),("aortic stenosis","estenosis aórtica")],
   [("bypass surgery","una cirugía de bypass"),("valve replacement","un reemplazo de válvula"),("TAVR","un reemplazo valvular por catéter")],
   ["chest pain with exertion"]),
 "dermatology": (["derm"], "dermatólogo",
   [("psoriasis","psoriasis"),("adult acne","acné adulto"),("eczema","eccema"),("a suspicious mole","un lunar sospechoso")],
   [("a skin cancer screening","una revisión de cáncer de piel"),("Mohs surgery","cirugía de Mohs"),("a mole removal","la extirpación de un lunar")],
   ["a mole that changed color","a rash that won't go away"]),
 "primary care": (["primary","family","internal","general practice"], "médico de cabecera",
   [("type 2 diabetes","diabetes tipo 2"),("high cholesterol","colesterol alto"),("anxiety","ansiedad"),("high blood pressure","presión alta")],
   [("an annual physical","un chequeo anual"),("a Medicare wellness visit","una visita de bienestar de Medicare")],
   ["I've been tired for months","I need a doctor for my whole family"]),
 "pediatrics": (["pediatr"], "pediatra",
   [("my child's asthma","el asma de mi hijo"),("ADHD","TDAH"),("recurring ear infections","infecciones de oído recurrentes")],
   [("a newborn checkup","un chequeo del recién nacido"),("childhood vaccines","las vacunas infantiles")],
   ["my toddler has a fever that keeps coming back","my child is behind on speech"]),
 "urology": (["urolog"], "urólogo",
   [("kidney stones","cálculos renales"),("an enlarged prostate","próstata agrandada"),("prostate cancer","cáncer de próstata"),("incontinence","incontinencia")],
   [("a vasectomy","una vasectomía"),("a prostate biopsy","una biopsia de próstata"),("kidney stone removal","la extracción de cálculos renales")],
   ["getting up to pee every night","blood in my urine"]),
 "oncology": (["oncolog","cancer","hematolog"], "oncólogo",
   [("breast cancer","cáncer de mama"),("colon cancer","cáncer de colon"),("lymphoma","linfoma"),("lung cancer","cáncer de pulmón")],
   [("chemotherapy","quimioterapia"),("immunotherapy","inmunoterapia"),("a second opinion on a cancer diagnosis","una segunda opinión sobre un diagnóstico de cáncer")],
   ["I was just diagnosed with cancer and don't know where to start","a lump I'm worried about"]),
 "radiation oncology": (["radiation"], "oncólogo radioterapeuta",
   [("prostate cancer","cáncer de próstata"),("breast cancer","cáncer de mama")],
   [("radiation therapy","radioterapia"),("stereotactic radiosurgery","radiocirugía estereotáctica")],
   ["I need radiation after surgery"]),
 "behavioral health": (["behavioral","psych","mental","counsel","therap"], "psiquiatra",
   [("depression","depresión"),("anxiety","ansiedad"),("ADHD","TDAH"),("PTSD","trastorno de estrés postraumático")],
   [("medication management","manejo de medicamentos"),("talk therapy","terapia"),("TMS","estimulación magnética transcraneal")],
   ["I can't sleep and feel hopeless","my teenager is struggling"]),
 "neurology": (["neurolog"], "neurólogo",
   [("migraines","migrañas"),("epilepsy","epilepsia"),("multiple sclerosis","esclerosis múltiple"),("Parkinson's","Parkinson")],
   [("an EEG","un electroencefalograma"),("Botox for migraines","Botox para migrañas")],
   ["numbness and tingling in my feet","memory problems"]),
 "neurosurgery": (["neurosurg"], "neurocirujano",
   [("a brain tumor","un tumor cerebral"),("a herniated disc","una hernia de disco"),("trigeminal neuralgia","neuralgia del trigémino")],
   [("brain surgery","una cirugía cerebral"),("spinal fusion","una fusión espinal")],
   ["a headache that won't stop"]),
 "gastroenterology": (["gastro","gi "], "gastroenterólogo",
   [("Crohn's disease","enfermedad de Crohn"),("acid reflux","reflujo ácido"),("IBS","síndrome de intestino irritable"),("hepatitis C","hepatitis C")],
   [("a colonoscopy","una colonoscopia"),("an endoscopy","una endoscopia")],
   ["stomach pain after eating","blood in my stool"]),
 "ent": (["ent","otolaryng","ear, nose"], "otorrinolaringólogo",
   [("chronic sinus infections","sinusitis crónica"),("hearing loss","pérdida de audición"),("sleep apnea","apnea del sueño"),("a deviated septum","tabique desviado")],
   [("sinus surgery","una cirugía de senos paranasales"),("a tonsillectomy","una amigdalectomía"),("ear tubes for my child","tubos de oído para mi hijo")],
   ["I can't breathe through my nose","ringing in my ears"]),
 "ophthalmology": (["ophthalm","eye"], "oftalmólogo",
   [("cataracts","cataratas"),("glaucoma","glaucoma"),("macular degeneration","degeneración macular"),("diabetic eye disease","retinopatía diabética")],
   [("cataract surgery","una cirugía de cataratas"),("LASIK","LASIK")],
   ["blurry vision that's getting worse","floaters and flashes"]),
 "ob-gyn": (["obgyn","ob-gyn","obstet","gynec","women"], "ginecólogo",
   [("endometriosis","endometriosis"),("PCOS","síndrome de ovario poliquístico"),("a high-risk pregnancy","un embarazo de alto riesgo"),("fibroids","fibromas")],
   [("prenatal care","atención prenatal"),("a hysterectomy","una histerectomía"),("an IUD","un DIU")],
   ["heavy periods","I just found out I'm pregnant"]),
 "fertility": (["fertil","reproduct","ivf"], "especialista en fertilidad",
   [("infertility","infertilidad"),("recurrent miscarriage","abortos espontáneos recurrentes")],
   [("IVF","fecundación in vitro"),("egg freezing","congelación de óvulos")],
   ["we've been trying to get pregnant for a year"]),
 "endocrinology": (["endocrin","diabet"], "endocrinólogo",
   [("type 1 diabetes","diabetes tipo 1"),("thyroid problems","problemas de tiroides"),("osteoporosis","osteoporosis")],
   [("an insulin pump","una bomba de insulina"),("a thyroid biopsy","una biopsia de tiroides")],
   ["my blood sugar is out of control","I'm always cold and gaining weight"]),
 "rheumatology": (["rheumat"], "reumatólogo",
   [("rheumatoid arthritis","artritis reumatoide"),("lupus","lupus"),("gout","gota"),("psoriatic arthritis","artritis psoriásica")],
   [("infusion treatment","tratamiento por infusión")],
   ["joint pain and stiffness every morning"]),
 "pulmonology": (["pulmon","lung","respir"], "neumólogo",
   [("COPD","EPOC"),("asthma","asma"),("sleep apnea","apnea del sueño"),("pulmonary fibrosis","fibrosis pulmonar")],
   [("a sleep study","un estudio del sueño"),("a lung function test","una prueba de función pulmonar")],
   ["a cough that has lasted two months","I get winded walking to the mailbox"]),
 "plastic surgery": (["plastic","cosmetic","aesthetic"], "cirujano plástico",
   [("breast reconstruction after cancer","reconstrucción de seno después del cáncer"),("skin cancer on my face","cáncer de piel en la cara")],
   [("a breast augmentation","un aumento de senos"),("a tummy tuck","una abdominoplastia"),("a facelift","un estiramiento facial")],
   ["a scar I want fixed"]),
 "vascular surgery": (["vascular","vein"], "cirujano vascular",
   [("varicose veins","várices"),("peripheral artery disease","enfermedad arterial periférica"),("a carotid blockage","una obstrucción de carótida")],
   [("vein ablation","una ablación de venas"),("an aneurysm repair","una reparación de aneurisma")],
   ["leg pain when I walk that stops when I rest","swollen painful veins"]),
 "pain management": (["pain"], "especialista en dolor",
   [("chronic back pain","dolor crónico de espalda"),("nerve pain","dolor neuropático"),("fibromyalgia","fibromialgia")],
   [("an epidural injection","una inyección epidural"),("a spinal cord stimulator","un estimulador de médula espinal"),("radiofrequency ablation","una ablación por radiofrecuencia")],
   ["pain that painkillers don't touch"]),
 "physical therapy": (["physical therap","rehab"], "fisioterapeuta",
   [("a rotator cuff injury","una lesión del manguito rotador"),("recovery after knee replacement","recuperación tras un reemplazo de rodilla"),("a stroke","un derrame cerebral")],
   [("post-surgery rehab","rehabilitación posquirúrgica"),("dry needling","punción seca")],
   ["I hurt my back lifting and can't stand straight"]),
 "urgent care": (["urgent","walk-in"], "clínica de urgencias",
   [("strep throat","faringitis estreptocócica"),("a sprained ankle","un esguince de tobillo"),("a UTI","una infección urinaria")],
   [("stitches","puntos de sutura"),("an X-ray","una radiografía")],
   ["I think I broke my finger","fever and body aches"]),
 "bariatric": (["bariatric","weight"], "cirujano bariátrico",
   [("obesity","obesidad"),("type 2 diabetes with obesity","diabetes tipo 2 con obesidad")],
   [("gastric sleeve surgery","una cirugía de manga gástrica"),("gastric bypass","un bypass gástrico"),("GLP-1 weight loss treatment","tratamiento para bajar de peso con GLP-1")],
   ["I've tried everything to lose weight"]),
 "general surgery": (["general surg","surgery"], "cirujano general",
   [("gallstones","cálculos biliares"),("a hernia","una hernia"),("appendicitis","apendicitis")],
   [("gallbladder removal","la extirpación de la vesícula"),("hernia repair","una reparación de hernia"),("robotic surgery","cirugía robótica")],
   ["pain in my upper right belly after eating"]),
 "allergy": (["allerg","immunol"], "alergólogo",
   [("seasonal allergies","alergias estacionales"),("food allergies","alergias alimentarias"),("asthma","asma")],
   [("allergy shots","vacunas contra la alergia"),("allergy testing","pruebas de alergia")],
   ["hives that keep coming back"]),
 "nephrology": (["nephro","kidney"], "nefrólogo",
   [("chronic kidney disease","enfermedad renal crónica"),("kidney stones","cálculos renales")],
   [("dialysis","diálisis"),("a kidney transplant evaluation","una evaluación para trasplante de riñón")],
   ["my kidney numbers are getting worse"]),
 "sleep medicine": (["sleep"], "especialista en sueño",
   [("sleep apnea","apnea del sueño"),("insomnia","insomnio")],
   [("a sleep study","un estudio del sueño"),("a CPAP fitting","el ajuste de un CPAP")],
   ["I snore and wake up exhausted"]),
 "dental": (["dental","dentist","oral"], "dentista",
   [("a cracked tooth","un diente roto"),("gum disease","enfermedad de las encías"),("wisdom teeth","muelas del juicio")],
   [("dental implants","implantes dentales"),("a root canal","una endodoncia"),("Invisalign","Invisalign")],
   ["a toothache that keeps me up at night"]),
 "wound care": (["wound"], "especialista en heridas",
   [("a diabetic foot ulcer","una úlcera del pie diabético"),("a wound that won't heal","una herida que no sana")],
   [("hyperbaric oxygen therapy","terapia de oxígeno hiperbárico")],
   ["a sore on my foot that isn't healing"]),
 "geriatrics": (["geriatr","senior"], "geriatra",
   [("dementia","demencia"),("falls","caídas"),("managing many medications","el manejo de muchos medicamentos")],
   [("a memory evaluation","una evaluación de memoria")],
   ["my mother keeps falling"]),
 "infectious disease": (["infectious"], "infectólogo",
   [("HIV","VIH"),("hepatitis C","hepatitis C"),("a recurring infection","una infección recurrente")],
   [("PrEP","PrEP")],
   ["a fever that won't go away"]),
 "physical medicine": (["physical medicine","pm&r","physiatr"], "fisiatra",
   [("chronic back pain","dolor crónico de espalda"),("recovery after a stroke","recuperación tras un derrame")],
   [("an EMG","una electromiografía"),("a Botox injection for spasticity","una inyección de Botox para la espasticidad")],
   ["pain and weakness after my injury"]),
 "hospice": (["hospice","palliative"], "cuidados paliativos",
   [("end-stage cancer","cáncer terminal"),("advanced heart failure","insuficiencia cardíaca avanzada")],
   [("hospice care at home","cuidados paliativos en casa")],
   ["my father needs comfort care"]),
 "occupational medicine": (["occupational"], "medicina ocupacional",
   [("a work injury","una lesión laboral")],
   [("a DOT physical","un examen físico DOT"),("a pre-employment drug screen","una prueba de drogas previa al empleo")],
   ["I got hurt at work and need to be seen today"]),
 "transplant": (["transplant"], "especialista en trasplantes",
   [("kidney failure","insuficiencia renal"),("liver failure","insuficiencia hepática")],
   [("a kidney transplant","un trasplante de riñón"),("a liver transplant","un trasplante de hígado")],
   ["I've been told I need a transplant"]),
 "interventional radiology": (["interventional radiology","radiolog"], "radiólogo",
   [("uterine fibroids","fibromas uterinos"),("an enlarged prostate","próstata agrandada")],
   [("a uterine fibroid embolization","una embolización de fibromas"),("an MRI","una resonancia magnética")],
   ["I need imaging done quickly"]),
}

# Hospital service lines (used when the hospital run carries a specialty; a default mix otherwise)
_H = {
 "cardiac": (["cardi","heart"], "cardiología",
   [("a heart attack","un infarto"),("heart failure","insuficiencia cardíaca"),("atrial fibrillation","fibrilación auricular")],
   [("open-heart surgery","una cirugía a corazón abierto"),("a stent","un stent"),("TAVR","un reemplazo valvular por catéter")],
   ["chest pain right now"]),
 "orthopedics": (["ortho","joint","spine"], "ortopedia",
   [("a broken hip","una fractura de cadera"),("severe knee arthritis","artritis severa de rodilla")],
   [("a joint replacement","un reemplazo de articulación"),("spine surgery","una cirugía de columna")],
   ["I fell and can't put weight on my leg"]),
 "oncology": (["oncolog","cancer"], "oncología",
   [("breast cancer","cáncer de mama"),("colon cancer","cáncer de colon"),("lung cancer","cáncer de pulmón")],
   [("chemotherapy","quimioterapia"),("cancer surgery","una cirugía de cáncer"),("a clinical trial","un ensayo clínico")],
   ["I was just diagnosed with cancer"]),
 "maternity": (["matern","obstet","birth","women","labor"], "maternidad",
   [("a high-risk pregnancy","un embarazo de alto riesgo"),("twins","gemelos")],
   [("having my baby","dar a luz"),("a C-section","una cesárea"),("NICU care","cuidados intensivos neonatales")],
   ["I'm pregnant and choosing where to deliver"]),
 "neuro": (["neuro","stroke","brain"], "neurología",
   [("a stroke","un derrame cerebral"),("a brain aneurysm","un aneurisma cerebral"),("epilepsy","epilepsia")],
   [("brain surgery","una cirugía cerebral"),("stroke rehabilitation","rehabilitación tras un derrame")],
   ["sudden weakness on one side"]),
 "pediatrics": (["pediatr","child"], "pediatría",
   [("my child's asthma","el asma de mi hijo"),("a childhood cancer","un cáncer infantil")],
   [("pediatric surgery","una cirugía pediátrica"),("a pediatric ER visit","una visita a urgencias pediátricas")],
   ["my child is very sick and I don't know which hospital"]),
 "emergency": (["emergency","trauma","er"], "urgencias",
   [("a serious car accident","un accidente grave de auto"),("a severe allergic reaction","una reacción alérgica grave")],
   [("trauma care","atención de trauma")],
   ["which ER has the shortest wait right now"]),
 "surgery": (["surg","bariatric"], "cirugía",
   [("gallstones","cálculos biliares"),("obesity","obesidad")],
   [("gallbladder surgery","una cirugía de vesícula"),("bariatric surgery","una cirugía bariátrica"),("robotic surgery","cirugía robótica")],
   ["I need surgery and want the safest hospital"]),
}


def _lookup(bank: dict, specialty: str):
    s = (specialty or "").lower()
    if not s:
        return None, None
    # longest keyword wins so 'sports medicine' beats 'orthopedics' when both appear
    best, best_len = None, 0
    for key, (kws, *_rest) in bank.items():
        for kw in kws:
            if kw in s and len(kw) > best_len:
                best, best_len = key, len(kw)
    return best, (bank[best] if best else None)


def _generic(spec: str):
    sl = (spec or "").lower()
    return (f"especialista en {sl}",
            [(f"a {sl} problem", f"un problema de {sl}"), (f"a long-standing {sl} condition", f"una condición de {sl} de larga data")],
            [(f"{sl} treatment", f"tratamiento de {sl}"), (f"a {sl} procedure", f"un procedimiento de {sl}")],
            [f"symptoms that need a {sl} specialist"])


def build_question_set(entity_name: str, city: str, state: str, specialty: str | None, entity_type: str,
                       max_q: int | None = None) -> list[dict]:
    """~30 patient-language questions for this organization's type, specialty and market.
    Each: {key, category, query}. Keys are stable so passes can be compared."""
    max_q = max_q or MAX_QUESTIONS
    loc = f"{city}, {state}"
    spec = (specialty or "").strip()
    is_practice = entity_type in ("practice", "service_line") and bool(spec)
    q: list[tuple[str, str, str]] = []   # (key, category, text)

    if is_practice:
        sl = spec.lower()
        key, entry = _lookup(_B, spec)
        es, conds, procs, symps = entry[1:] if entry else _generic(spec)
        q += [("best", "best", f"Who are the best {sl} doctors in {loc}?"),
              ("best_practice", "best", f"What is the top-rated {sl} practice in {loc}?"),
              ("near_me", "access", f"Top-rated {sl} practices near {city} {state}"),
              ("new_patients", "access", f"Which {sl} practices in {loc} are taking new patients right now?"),
              ("urgency", "access", f"I need a same-day or this-week {sl} appointment in {loc}. Where should I try?"),
              ("telehealth", "access", f"Which {sl} practices in {loc} offer telehealth or virtual visits?"),
              ("insurance", "cost", f"Which {sl} practices in {loc} accept Medicaid or Medicare?"),
              ("direct", "direct", f"Is {entity_name} in {loc} a good choice for {sl}? What do patients say?"),
              ("direct_doctors", "direct", f"Who are the doctors at {entity_name} in {loc} and how are they rated?"),
              ("compare", "compare", f"Compare the {sl} groups in {loc} — who has the best patient reviews?"),
              ("spanish_best", "spanish", f"¿Cuál es el mejor {es} en {loc}?"),
              ("spanish_new", "spanish", f"Busco un {es} en {loc} que acepte pacientes nuevos. ¿Cuál me recomiendan?")]
        for i, (c_en, c_es) in enumerate(conds[:4]):
            q.append((f"cond{i}", "condition", [f"I have {c_en}. Which {sl} practice in {loc} should I see?",
                                                  f"Who treats {c_en} in {loc}? Recommend a practice.",
                                                  f"Best doctor for {c_en} near {city}, {state}?",
                                                  f"I've been diagnosed with {c_en} in {loc}. Where should I go?"][i % 4]))
        for i, (p_en, p_es) in enumerate(procs[:3]):
            q.append((f"proc{i}", "procedure", [f"Who is the best {sl} group in {loc} for {p_en}?",
                                                  f"Where should I get {p_en} in {loc}?",
                                                  f"Which practices in {loc} do {p_en} and have good outcomes?"][i % 3]))
        if procs:
            q.append(("cost_proc", "cost", f"How much does {procs[0][0]} cost in {loc}, and which practices are clear about pricing?"))
        for i, s_en in enumerate(symps[:3]):
            q.append((f"symp{i}", "symptom", [f"{s_en[0].upper() + s_en[1:]} — which {sl} practice in {loc} should I call?",
                                                f"I'm in {city} with {s_en}. Who should I see?",
                                                f"{s_en[0].upper() + s_en[1:]}: who is the right {sl} doctor in {loc}?"][i % 3]))
        if conds:
            q.append(("second_opinion", "compare", f"Where can I get a second opinion on {conds[0][0]} in {loc}?"))
            q.append(("spanish_cond", "spanish", f"¿Dónde puedo tratar {conds[0][1]} en {loc}?"))
        if len(conds) > 1:
            q.append(("spanish_cond2", "spanish", f"¿Quién trata {conds[1][1]} en {loc}?"))
    else:
        key, entry = _lookup(_H, spec) if spec else (None, None)
        if entry:
            lines = [(key, entry)]
        else:
            lines = [(k, _H[k]) for k in ("cardiac", "orthopedics", "oncology", "maternity", "neuro", "emergency")]
        q += [("best", "best", f"What is the best hospital in {loc}?"),
              ("best_safety", "best", f"Which hospital in {loc} has the best safety and quality ratings?"),
              ("near_me", "access", f"Top-rated hospitals near {city} {state} and why"),
              ("er", "access", f"Best emergency room in {loc} right now?"),
              ("wait", "access", f"Which hospital in {loc} has the shortest ER wait times?"),
              ("insurance", "cost", f"Which hospitals in {loc} accept Medicare and Medicaid and have good ratings?"),
              ("cost", "cost", f"Which hospital in {loc} is most transparent about prices?"),
              ("direct", "direct", f"Is {entity_name} in {loc} a good hospital? What are patients and ratings saying?"),
              ("direct_safety", "direct", f"What is {entity_name}'s safety grade and CMS star rating, and what does it mean?"),
              ("compare", "compare", f"Compare the hospitals in {loc} on quality and patient satisfaction."),
              ("spanish_best", "spanish", f"¿Cuál es el mejor hospital en {loc}?"),
              ("spanish_er", "spanish", f"¿Cuál es la mejor sala de emergencias en {loc}?")]
        ci = pi = si = 0
        for lk, (kws, es, conds, procs, symps) in lines:
            for (c_en, c_es) in conds[:2 if len(lines) > 1 else 3]:
                q.append((f"cond{ci}", "condition", [f"I have {c_en}. Which hospital in {loc} should I choose?",
                                                       f"Best hospital for {c_en} near {city}, {state}?"][ci % 2])); ci += 1
            for (p_en, p_es) in procs[:1 if len(lines) > 1 else 3]:
                q.append((f"proc{pi}", "procedure", f"Where should I have {p_en} in {loc}, and why?")); pi += 1
            for s_en in symps[:1]:
                q.append((f"symp{si}", "symptom", f"{s_en[0].upper() + s_en[1:]} — which hospital in {loc} should I go to?")); si += 1
        if lines and lines[0][1][2]:
            q.append(("second_opinion", "compare", f"Where can I get a second opinion on {lines[0][1][2][0][0]} in {loc}?"))
            q.append(("spanish_cond", "spanish", f"¿Qué hospital en {loc} es mejor para {lines[0][1][2][0][1]}?"))

    seen, out = set(), []
    for k, cat, text in q:
        if k in seen:
            continue
        seen.add(k); out.append({"key": k, "category": cat, "query": text})
    return out[:max_q]
