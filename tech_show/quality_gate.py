"""Conservative, deterministic prerequisites for a future publisher.

Passing this gate is necessary but NOT sufficient for publication: an
independent editorial and media-rights review is still required.
"""
from __future__ import annotations
import re
from urllib.parse import urlsplit

APPROVED_LICENSES={'CC0-1.0','CC-BY-4.0'}
PRODUCT_TYPES={'photo','video'}

def normalize(s):return ' '.join(re.findall(r'[\w]+',(s or '').casefold()))

def https_url(value):
 try:
  p=urlsplit(value);return p.scheme=='https' and bool(p.hostname) and not p.username and not p.password
 except ValueError:return False

def validate(manifest: dict) -> list[str]:
    errors=[];name=manifest.get('product_name','').strip();sources=manifest.get('official_sources',[])
    if len(name)<3:errors.append('missing_product_name')
    if not isinstance(sources,list) or not sources or any(not https_url(x) for x in sources):errors.append('official_source_urls_required')
    if manifest.get('editorial_review')!='passed':errors.append('editorial_review_required')
    if manifest.get('rights_review')!='passed':errors.append('rights_review_required')
    if manifest.get('media_authenticity_review')!='passed':errors.append('authenticity_review_required')
    assets=manifest.get('media',[])
    if not isinstance(assets,list):assets=[];errors.append('invalid_media')
    used=set();counts={'photo':0,'video':0}
    for item in assets:
        if not isinstance(item,dict):errors.append('invalid_media_item');continue
        kind=item.get('type');url=item.get('source_url','');ident=item.get('depicts_product','')
        if kind not in PRODUCT_TYPES:errors.append('invalid_media_type');continue
        if not https_url(url) or url in used:errors.append('invalid_or_duplicate_media_source');continue
        used.add(url)
        if normalize(ident)!=normalize(name):errors.append('media_product_mismatch');continue
        if item.get('license') not in APPROVED_LICENSES or not https_url(item.get('license_url','')):errors.append('unapproved_media_license');continue
        if item.get('license')=='CC-BY-4.0' and not item.get('author'):errors.append('missing_media_author');continue
        if item.get('rights_status')!='verified':errors.append('media_rights_not_verified');continue
        if item.get('authenticity')!='verified':errors.append('media_authenticity_not_verified');continue
        counts[kind]+=1
    if counts['photo']<2:errors.append('two_verified_product_photos_required')
    if counts['video']<1:errors.append('verified_product_video_required')
    for language in ('english','hindi'):
        v=manifest.get('videos',{}).get(language,{})
        try: duration=float(v.get('duration_seconds',0))
        except (TypeError,ValueError):duration=0
        if not 360<=duration<=600:errors.append(language+'_duration_out_of_range')
        if v.get('narration_language')!=language:errors.append(language+'_narration_mismatch')
        if v.get('visual_review')!='passed':errors.append(language+'_visual_review_required')
        if v.get('audio_review')!='passed':errors.append(language+'_audio_review_required')
    cap=manifest.get('caption','')
    if not isinstance(cap,str) or not cap.strip() or len(cap)>2200:errors.append('caption_missing_or_too_long')
    elif not all(re.search(r'\b'+x+r'\b',cap,re.I) for x in ('english','roman')):errors.append('bilingual_caption_required')
    if manifest.get('poster',{}).get('visual_review')!='passed':errors.append('poster_review_required')
    return errors
