"""Controlled specialty labels.

Free-text specialties ("ortho", "Orthopaedics", "Orthopedic Surgery") print the same way on the
cover, in file names, in History and in the spot-check question bank: one canonical label per
specialty. Unknown specialties keep the user's wording, title-cased.
"""
from __future__ import annotations

import re

# keyword → canonical label. Longest matching keyword wins; keywords of three letters or
# fewer match on word boundaries only ("gi", "ent").
_SYNONYMS: list[tuple[str, str]] = [
    ("orthodont", "Orthodontics"),
    ("ortho", "Orthopedics"), ("orthopaed", "Orthopedics"), ("orthoped", "Orthopedics"), ("musculoskeletal", "Orthopedics"),
    ("sports", "Sports Medicine"),
    ("spine", "Spine"), ("spinal", "Spine"),
    ("hand surg", "Hand Surgery"), ("hand & upper", "Hand Surgery"), ("hand and upper", "Hand Surgery"),
    ("podiat", "Podiatry"), ("foot & ankle", "Podiatry"), ("foot and ankle", "Podiatry"),
    ("cardiothoracic", "Cardiothoracic Surgery"), ("cardiac surg", "Cardiothoracic Surgery"), ("heart surg", "Cardiothoracic Surgery"),
    ("cardio", "Cardiology"), ("cardiac", "Cardiology"), ("heart", "Cardiology"),
    ("derm", "Dermatology"),
    ("family", "Family Medicine"),
    ("internal med", "Internal Medicine"), ("internist", "Internal Medicine"),
    ("primary", "Primary Care"), ("general practice", "Primary Care"), ("gp", "Primary Care"),
    ("pediatr", "Pediatrics"), ("paediatr", "Pediatrics"), ("children", "Pediatrics"),
    ("urolog", "Urology"),
    ("radiation onc", "Radiation Oncology"), ("radiation", "Radiation Oncology"),
    ("oncolog", "Oncology"), ("cancer", "Oncology"),
    ("hematolog", "Hematology"),
    ("psychiat", "Psychiatry"),
    ("behavioral", "Behavioral Health"), ("mental health", "Behavioral Health"), ("counsel", "Behavioral Health"), ("psycholog", "Behavioral Health"),
    ("neurosurg", "Neurosurgery"),
    ("neuro", "Neurology"),
    ("gastro", "Gastroenterology"), ("gi", "Gastroenterology"), ("digestive", "Gastroenterology"),
    ("otolaryng", "Otolaryngology (ENT)"), ("ent", "Otolaryngology (ENT)"), ("ear, nose", "Otolaryngology (ENT)"), ("ear nose", "Otolaryngology (ENT)"), ("head & neck", "Otolaryngology (ENT)"),
    ("ophthalm", "Ophthalmology"), ("eye", "Ophthalmology"), ("retina", "Ophthalmology"),
    ("optomet", "Optometry"),
    ("fertil", "Fertility"), ("reproduct", "Fertility"), ("ivf", "Fertility"),
    ("endocrin", "Endocrinology"), ("diabet", "Endocrinology"),
    ("rheumat", "Rheumatology"),
    ("pulmon", "Pulmonology"), ("lung", "Pulmonology"), ("respir", "Pulmonology"),
    ("plastic", "Plastic Surgery"), ("cosmetic", "Plastic Surgery"), ("aesthetic", "Plastic Surgery"),
    ("vascular", "Vascular Surgery"), ("vein", "Vascular Surgery"),
    ("pain", "Pain Management"),
    ("physical therap", "Physical Therapy"), ("physiotherap", "Physical Therapy"),
    ("physical medicine", "Physical Medicine & Rehabilitation"), ("physiatr", "Physical Medicine & Rehabilitation"), ("pm&r", "Physical Medicine & Rehabilitation"), ("rehab", "Physical Medicine & Rehabilitation"),
    ("urgent", "Urgent Care"), ("walk-in", "Urgent Care"),
    ("bariatric", "Bariatric Surgery"), ("weight loss", "Bariatric Surgery"),
    ("general surg", "General Surgery"),
    ("allerg", "Allergy & Immunology"), ("immunol", "Allergy & Immunology"),
    ("nephro", "Nephrology"), ("kidney", "Nephrology"),
    ("sleep", "Sleep Medicine"),
    ("dental", "Dentistry"), ("dentist", "Dentistry"),
    ("oral surg", "Oral & Maxillofacial Surgery"), ("maxillofacial", "Oral & Maxillofacial Surgery"),
    ("wound", "Wound Care"),
    ("geriatr", "Geriatrics"), ("senior", "Geriatrics"),
    ("infectious", "Infectious Disease"),
    ("hospice", "Hospice & Palliative Care"), ("palliative", "Hospice & Palliative Care"),
    ("occupational", "Occupational Medicine"),
    ("transplant", "Transplant"),
    ("radiolog", "Radiology"), ("imaging", "Radiology"),
    ("obstet", "Obstetrics & Gynecology (OB/GYN)"), ("gynec", "Obstetrics & Gynecology (OB/GYN)"), ("ob/gyn", "Obstetrics & Gynecology (OB/GYN)"),
    ("obgyn", "Obstetrics & Gynecology (OB/GYN)"), ("ob-gyn", "Obstetrics & Gynecology (OB/GYN)"), ("women", "Obstetrics & Gynecology (OB/GYN)"),
    ("chiropract", "Chiropractic"),
    ("audiolog", "Audiology"), ("hearing", "Audiology"),
    ("anesthes", "Anesthesiology"),
    ("emergency", "Emergency Medicine"),
    ("student health", "Student Health"),
]
CANONICAL: list[str] = sorted({label for _, label in _SYNONYMS})


def normalize_specialty(text: str | None) -> str | None:
    """Canonical label for a free-text specialty; None/'' pass through; unknown text is title-cased."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return raw
    s = raw.lower()
    best, best_len = None, 0
    for kw, label in _SYNONYMS:
        hit = re.search(r"\b" + re.escape(kw) + r"\b", s) if len(kw) <= 3 else (kw in s)
        if hit and len(kw) > best_len:
            best, best_len = label, len(kw)
    if best:
        return best
    return " ".join(w if (w.isupper() and len(w) <= 5) else w.capitalize() for w in raw.split())


# ── schema.org MedicalSpecialty (3.3) ─────────────────────────────────────────
# The enumeration as published at https://schema.org/MedicalSpecialty (checked 2026-10-16).
# There is no "Orthopedic" member: orthopaedics maps to Musculoskeletal, plus Surgical for a
# surgical group. Drafted JSON-LD may only use values from this set.
SCHEMA_MEDICAL_SPECIALTIES = frozenset({
    "Anesthesia", "Audiology", "Cardiovascular", "CommunityHealth", "Dentistry", "Dermatology", "DietNutrition",
    "Emergency", "Endocrine", "Gastroenterologic", "Genetic", "Geriatric", "Gynecologic", "Hematologic", "Infectious",
    "LaboratoryScience", "Midwifery", "Musculoskeletal", "Neurologic", "Nursing", "Obstetric", "Oncologic",
    "Ophthalmology", "Optometric", "Otolaryngologic", "Pathology", "Pediatric", "PharmacySpecialty", "Physiotherapy",
    "PlasticSurgery", "Podiatric", "PrimaryCare", "Psychiatric", "PublicHealth", "Pulmonary", "Radiography", "Renal",
    "RespiratoryTherapy", "Rheumatologic", "SpeechPathology", "Surgical", "Toxicologic", "Urologic",
})

# Pulse canonical specialty → schema.org values. (surgical: bool) — Surgical is added for a surgical group.
_SCHEMA_MAP: dict[str, tuple[list[str], bool]] = {
    "Orthopedics": (["Musculoskeletal"], False), "Sports Medicine": (["Musculoskeletal"], False),
    "Spine": (["Musculoskeletal"], False), "Hand Surgery": (["Musculoskeletal", "Surgical"], True),
    "Podiatry": (["Podiatric"], False), "Chiropractic": (["Musculoskeletal"], False),
    "Cardiothoracic Surgery": (["Cardiovascular", "Surgical"], True), "Cardiology": (["Cardiovascular"], False),
    "Vascular Surgery": (["Cardiovascular", "Surgical"], True), "Dermatology": (["Dermatology"], False),
    "Family Medicine": (["PrimaryCare"], False), "Internal Medicine": (["PrimaryCare"], False),
    "Primary Care": (["PrimaryCare"], False), "Student Health": (["PrimaryCare"], False), "Pediatrics": (["Pediatric"], False),
    "Urology": (["Urologic"], False), "Radiation Oncology": (["Oncologic"], False), "Oncology": (["Oncologic"], False),
    "Hematology": (["Hematologic"], False), "Psychiatry": (["Psychiatric"], False), "Behavioral Health": (["Psychiatric"], False),
    "Neurosurgery": (["Neurologic", "Surgical"], True), "Neurology": (["Neurologic"], False),
    "Gastroenterology": (["Gastroenterologic"], False), "Otolaryngology (ENT)": (["Otolaryngologic"], False),
    "Ophthalmology": (["Ophthalmology"], False), "Optometry": (["Optometric"], False), "Fertility": (["Gynecologic"], False),
    "Endocrinology": (["Endocrine"], False), "Rheumatology": (["Rheumatologic"], False), "Pulmonology": (["Pulmonary"], False),
    "Plastic Surgery": (["PlasticSurgery"], True), "Physical Therapy": (["Physiotherapy"], False),
    "Physical Medicine & Rehabilitation": (["Physiotherapy"], False), "Bariatric Surgery": (["Surgical"], True),
    "General Surgery": (["Surgical"], True), "Nephrology": (["Renal"], False), "Dentistry": (["Dentistry"], False),
    "Orthodontics": (["Dentistry"], False), "Oral & Maxillofacial Surgery": (["Dentistry", "Surgical"], True),
    "Geriatrics": (["Geriatric"], False), "Infectious Disease": (["Infectious"], False), "Transplant": (["Surgical"], True),
    "Radiology": (["Radiography"], False), "Obstetrics & Gynecology (OB/GYN)": (["Obstetric", "Gynecologic"], False),
    "Audiology": (["Audiology"], False), "Anesthesiology": (["Anesthesia"], False), "Emergency Medicine": (["Emergency"], False),
}


def schema_specialties(specialty: str | None, surgical: bool = False) -> list[str]:
    """Valid schema.org MedicalSpecialty values for a Pulse specialty, in order; [] when the
    vocabulary has no fitting member (the draft then omits medicalSpecialty rather than invent one).
    `surgical` adds Surgical for a surgical group (procedural weighting) when the specialty is not
    surgical by definition."""
    canon = normalize_specialty(specialty) or (specialty or "").strip()
    vals, inherently = _SCHEMA_MAP.get(canon, ([], False))
    out = list(vals)
    if surgical and out and "Surgical" not in out and not inherently and canon in (
            "Orthopedics", "Spine", "Sports Medicine", "Urology", "Otolaryngology (ENT)", "Ophthalmology",
            "Obstetrics & Gynecology (OB/GYN)", "Gastroenterology", "Cardiology", "Podiatry", "Dentistry"):
        out.append("Surgical")
    return [v for v in out if v in SCHEMA_MEDICAL_SPECIALTIES]
