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
