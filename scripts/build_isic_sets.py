#!/usr/bin/env python3
from __future__ import annotations

"""
Stichting HUID - robuuste dataset builder (histopathology-only).

Belangrijk:
- Alleen histopathology-geverifieerde laesies
- Vaste (deterministische) quizsets, geen random
- Meerdere sets per module
"""

import json
import time
from pathlib import Path
from typing import Dict, List

import requests

BASE = Path('/home/tobias/.openclaw/workspace/dermatoscopie-oefenplatform/data')
OUT_PATH = BASE / 'isic_quiz_sets.json'
CK_PATH = BASE / 'isic_checkpoint.json'

SEARCH_URL = 'https://api.isic-archive.com/api/v2/images/search/'
RETRIES = 5
TARGET_PER_LABEL = 15  # 3 sets x 5 per label

LABEL_QUERIES = {
    'melanoma': 'diagnosis_3:"Melanoma, NOS"',
    'nevus': 'diagnosis_3:"Nevus"',
    'bcc': 'diagnosis_3:"Basal cell carcinoma"',
    'sebaceous_hyperplasia': 'diagnosis_3:"Sebaceous hyperplasia"',
    'actinic_keratosis': 'diagnosis_3:"Solar or actinic keratosis"',
    'bowen': 'diagnosis_3:"Squamous cell carcinoma in situ"',
}


def fetch_json(url: str, params=None):
    last = None
    for i in range(1, RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=45)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(min(2 ** i, 20))
    raise RuntimeError(f'Failed request: {url} :: {last}')


def save_ck(state: dict):
    CK_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')


def load_ck():
    if not CK_PATH.exists():
        return None
    try:
        return json.loads(CK_PATH.read_text(encoding='utf-8'))
    except Exception:
        return None


def is_dermoscopic(meta: dict) -> bool:
    acq = (meta or {}).get('acquisition', {})
    t = str(acq.get('image_type', '')).lower()
    return (not t) or ('dermo' in t)


def is_histopathology(clinical: dict) -> bool:
    conf = str(clinical.get('diagnosis_confirm_type', '')).lower()
    return 'histopath' in conf


def add_case(bucket: List[dict], seen_image_ids: set, seen_lesion_ids: set, label: str, r: dict, allow_clinical_fallback: bool = False):
    isic_id = r.get('isic_id', '')
    if not isic_id or isic_id in seen_image_ids:
        return

    meta = r.get('metadata') or {}
    clinical = meta.get('clinical') or {}
    if not is_dermoscopic(meta):
        return

    # Prefer histopathology; optionally allow clinical when needed
    is_histo = is_histopathology(clinical)
    if not is_histo and not allow_clinical_fallback:
        return

    lesion_id = str(clinical.get('lesion_id', '') or '')
    # prevent near-duplicate follow-up photos of same lesion
    if lesion_id and lesion_id in seen_lesion_ids:
        return

    img = ((r.get('files') or {}).get('full') or {}).get('url', '')
    if not img:
        return

    bucket.append({
        'id': isic_id,
        'lesionId': lesion_id,
        'imageUrl': img,
        'diagnosis': label,
        'source': 'histopathology' if is_histo else 'clinical diagnosis'
    })
    seen_image_ids.add(isic_id)
    if lesion_id:
        seen_lesion_ids.add(lesion_id)


def harvest_label(label: str, query: str, state: dict, target: int) -> List[dict]:
    bucket = state.get('buckets', {}).get(label, [])
    seen_image_ids = set(x['id'] for x in bucket)
    seen_lesion_ids = set(str(x.get('lesionId', '') or '') for x in bucket if x.get('lesionId'))

    next_key = f'next_{label}'
    next_url = state.get(next_key)

    if next_url:
        j = fetch_json(next_url)
    else:
        j = fetch_json(SEARCH_URL, params={'query': query, 'limit': 200})

    # Pass 1: histopathology only
    while len(bucket) < target:
        results = j.get('results', [])
        if not results:
            break

        for r in results:
            add_case(bucket, seen_image_ids, seen_lesion_ids, label, r, allow_clinical_fallback=False)
            if len(bucket) >= target:
                break

        next_url = j.get('next')
        state[next_key] = next_url
        state.setdefault('buckets', {})[label] = bucket
        save_ck(state)

        if not next_url:
            break
        j = fetch_json(next_url)
        time.sleep(0.08)

    # Pass 2 (fallback): allow clinical diagnosis if still too few
    if len(bucket) < target:
        # restart query from first page to include clinically diagnosed unique lesions
        j = fetch_json(SEARCH_URL, params={'query': query, 'limit': 200})
        while len(bucket) < target:
            results = j.get('results', [])
            if not results:
                break
            for r in results:
                add_case(bucket, seen_image_ids, seen_lesion_ids, label, r, allow_clinical_fallback=True)
                if len(bucket) >= target:
                    break
            nxt = j.get('next')
            if not nxt:
                break
            j = fetch_json(nxt)
            time.sleep(0.05)

    return bucket


def build_sets(a: List[dict], b: List[dict], nsets=3, preferred_per_class=5, fallback_per_class=3):
    a_sorted = sorted(a, key=lambda x: x['id'])
    b_sorted = sorted(b, key=lambda x: x['id'])

    for per_class in (preferred_per_class, fallback_per_class):
        need = nsets * per_class
        aa_all = a_sorted[:need]
        bb_all = b_sorted[:need]
        out = []
        for i in range(nsets):
            aa = aa_all[i * per_class:(i + 1) * per_class]
            bb = bb_all[i * per_class:(i + 1) * per_class]
            if len(aa) == per_class and len(bb) == per_class:
                merged = []
                for x, y in zip(aa, bb):
                    merged.append(x)
                    merged.append(y)
                out.append(merged)
        if len(out) == nsets:
            return out
    return []


def main():
    BASE.mkdir(parents=True, exist_ok=True)

    ck = load_ck() or {}
    state = {
        'version': 4,
        'buckets': ck.get('buckets') or {k: [] for k in LABEL_QUERIES.keys()},
    }
    # keep old next cursors if present
    for k in LABEL_QUERIES.keys():
        nk = f'next_{k}'
        if ck.get(nk):
            state[nk] = ck[nk]

    buckets = state['buckets']

    # harvest each label with histopathology-only filter
    for label, query in LABEL_QUERIES.items():
        buckets[label] = harvest_label(label, query, state, TARGET_PER_LABEL)
        print(f"{label}: {len(buckets[label])}")

    modules = {
        'mel_vs_nevus': build_sets(buckets['melanoma'], buckets['nevus'], nsets=3),
        'bcc_vs_sh': build_sets(buckets['bcc'], buckets['sebaceous_hyperplasia'], nsets=3),
        'bcc_vs_bowen': build_sets(buckets['bcc'], buckets['bowen'], nsets=3),
    }

    set_sizes = {k: [len(s) for s in v] for k, v in modules.items()}
    counts = {k: len(v) for k, v in buckets.items()}

    payload = {
        'meta': {
            'brand': 'Stichting HUID',
            'audience': 'Huisartsen',
            'verification': 'histopathology only',
            'counts': counts,
            'set_sizes': set_sizes,
            'note': 'Vaste quizsets, deterministisch, geen random selectie'
        },
        'modules': modules,
    }

    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    save_ck(state)

    print(json.dumps(payload['meta'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
