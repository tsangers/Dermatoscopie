#!/usr/bin/env python3
from __future__ import annotations

"""
Robuust dataset-script voor Stichting HUID dermatoscopie-oefenplatform.

Doel:
- Vaste (deterministische) quizsets genereren
- Geen random samenstelling in de app
- Resume/checkpoint + retry

Bronnen:
- ISIC API v2 lesions endpoint (sterk voor melanoma/nevus/bcc/AK)
- ISIC API v2 image search endpoint (voor sebaceous hyperplasia en bowen/scc in situ)
"""

import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import requests

BASE = Path('/home/tobias/.openclaw/workspace/dermatoscopie-oefenplatform/data')
OUT_PATH = BASE / 'isic_quiz_sets.json'
CK_PATH = BASE / 'isic_checkpoint.json'

RETRIES = 5
TARGET_PER_LABEL = 15  # 3 sets x 5 per klasse

LABELS = ['melanoma', 'nevus', 'bcc', 'sebaceous_hyperplasia', 'actinic_keratosis', 'bowen']

LESIONS_URL = 'https://api.isic-archive.com/api/v2/lesions/?limit=200'
SEARCH_URL = 'https://api.isic-archive.com/api/v2/images/search/'


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


def lesion_to_case(img: dict, label: str) -> dict | None:
    image_url = ((img.get('files') or {}).get('full') or {}).get('url', '')
    isic_id = img.get('isic_id', '')
    if not image_url or not isic_id:
        return None
    if not is_dermoscopic(img.get('metadata') or {}):
        return None
    return {
        'id': isic_id,
        'imageUrl': image_url,
        'diagnosis': label,
        'source': 'APIv2-lesions'
    }


def add_case(buckets: Dict[str, List[dict]], counts: Dict[str, int], label: str, case: dict | None):
    if not case:
        return
    if counts[label] >= TARGET_PER_LABEL:
        return
    if any(x['id'] == case['id'] for x in buckets[label]):
        return
    buckets[label].append(case)
    counts[label] += 1


def done_pair(counts, a, b):
    return counts[a] >= TARGET_PER_LABEL and counts[b] >= TARGET_PER_LABEL


def done_all(counts):
    return all(counts[l] >= TARGET_PER_LABEL for l in LABELS)


def map_lesion_label(lesion: dict) -> str | None:
    txt = ' | '.join([
        str(lesion.get('outcome_diagnosis', '')),
        str(lesion.get('outcome_diagnosis_1', '')),
    ]).lower()

    if 'actinic keratosis' in txt:
        return 'actinic_keratosis'
    if 'basal cell carcinoma' in txt:
        return 'bcc'
    if 'melanoma' in txt:
        return 'melanoma'
    if 'nevus' in txt or 'naevus' in txt:
        return 'nevus'
    if 'bowen' in txt or 'squamous cell carcinoma in situ' in txt:
        return 'bowen'
    return None


def harvest_from_lesions(state, buckets, counts):
    url = state.get('lesions_url') or LESIONS_URL
    scanned = int(state.get('scanned_lesions', 0))

    while url and scanned < 50000 and not (
        done_pair(counts, 'melanoma', 'nevus') and
        counts['bcc'] >= TARGET_PER_LABEL and
        counts['actinic_keratosis'] >= TARGET_PER_LABEL
    ):
        j = fetch_json(url)
        res = j.get('results', [])
        if not res:
            break

        for lesion in res:
            scanned += 1
            label = map_lesion_label(lesion)
            if label in ('melanoma', 'nevus', 'bcc', 'actinic_keratosis'):
                index_id = str(lesion.get('index_image_id') or '')
                imgs = lesion.get('images', []) or []
                pick = None
                for im in imgs:
                    if str(im.get('isic_id', '')) == index_id:
                        pick = im
                        break
                if pick is None and imgs:
                    pick = imgs[0]
                if pick is not None:
                    case = lesion_to_case(pick, label)
                    add_case(buckets, counts, label, case)

        url = j.get('next')
        state['lesions_url'] = url
        state['scanned_lesions'] = scanned

        if scanned % 1000 < len(res):
            print(f"lesions scanned={scanned} counts=" + json.dumps(dict(counts), ensure_ascii=False))
            state['counts'] = dict(counts)
            state['buckets'] = buckets
            save_ck(state)
            time.sleep(0.1)


def harvest_by_search(label: str, query: str, state, buckets, counts):
    k = f'search_url_{label}'
    next_url = state.get(k)

    if next_url:
        j = fetch_json(next_url)
    else:
        j = fetch_json(SEARCH_URL, params={'query': query, 'limit': 200})

    while counts[label] < TARGET_PER_LABEL:
        res = j.get('results', [])
        if not res:
            break

        for r in res:
            case = {
                'id': r.get('isic_id', ''),
                'imageUrl': ((r.get('files') or {}).get('full') or {}).get('url', ''),
                'diagnosis': label,
                'source': 'APIv2-search'
            }
            # filter dermoscopic
            if not is_dermoscopic((r.get('metadata') or {})):
                continue
            add_case(buckets, counts, label, case)
            if counts[label] >= TARGET_PER_LABEL:
                break

        next_url = j.get('next')
        state[k] = next_url
        state['counts'] = dict(counts)
        state['buckets'] = buckets
        save_ck(state)

        if not next_url:
            break
        j = fetch_json(next_url)
        time.sleep(0.1)


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
    return out


def main():
    BASE.mkdir(parents=True, exist_ok=True)

    ck = load_ck() or {}
    buckets = ck.get('buckets') or {l: [] for l in LABELS}
    counts = defaultdict(int, ck.get('counts') or {l: 0 for l in LABELS})

    state = {
        'version': 3,
        'target_per_label': TARGET_PER_LABEL,
        'lesions_url': ck.get('lesions_url'),
        'scanned_lesions': ck.get('scanned_lesions', 0),
        'search_url_sebaceous_hyperplasia': ck.get('search_url_sebaceous_hyperplasia'),
        'search_url_bowen': ck.get('search_url_bowen'),
        'counts': dict(counts),
        'buckets': buckets,
    }

    # 1) lesions stream for melanoma/nevus/bcc/AK
    harvest_from_lesions(state, buckets, counts)

    # 2) targeted search for sebaceous hyperplasia and bowen
    harvest_by_search('sebaceous_hyperplasia', 'diagnosis_3:"Sebaceous hyperplasia"', state, buckets, counts)
    harvest_by_search('bowen', 'diagnosis_3:"Squamous cell carcinoma in situ"', state, buckets, counts)

    modules = {
        'mel_vs_nevus': build_sets(buckets['melanoma'], buckets['nevus'], nsets=3, preferred_per_class=5, fallback_per_class=3),
        'bcc_vs_sh': build_sets(buckets['bcc'], buckets['sebaceous_hyperplasia'], nsets=3, preferred_per_class=5, fallback_per_class=3),
        'ak_vs_bowen': build_sets(buckets['actinic_keratosis'], buckets['bowen'], nsets=3, preferred_per_class=5, fallback_per_class=3),
    }

    set_sizes = {k: [len(s) for s in v] for k, v in modules.items()}

    payload = {
        'meta': {
            'brand': 'Stichting HUID',
            'audience': 'Huisartsen / AIOS dermatologie',
            'target_per_label': TARGET_PER_LABEL,
            'counts': dict(counts),
            'scanned_lesions': state.get('scanned_lesions', 0),
            'set_sizes': set_sizes,
            'note': 'Vaste quizsets (niet random)'
        },
        'modules': modules,
    }

    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    state['counts'] = dict(counts)
    state['buckets'] = buckets
    save_ck(state)

    print(json.dumps(payload['meta'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
