"""Narrow, manually reviewed stock images. Never pretend to depict a news event.

The source pages and Pixabay license were checked; the bundled image bytes are
hash-pinned. Only explicitly related headlines qualify. Expand this library with
individually reviewed assets, never with unchecked image-search results.
"""
from pathlib import Path
import re
from .util import sha256_file

ASSETS = Path(__file__).parent / 'assets' / 'curated_photos'
LICENSE = 'https://pixabay.com/service/terms/'
PHOTOS = {
 'paraguay_presidency': ('paraguay_presidency.jpg','24e66371ab301ec594bfe7fff568488fadb9b5c5e295b491efd64682ae92832e','https://pixabay.com/photos/paraguay-presidential-palace-palace-2211577/','Viktor_Kisman'),
 'firecrackers': ('firecrackers.jpg','0a811d169136c8f78e3fa204daa3296ddaf9482d188ecc07d3197f6684b97371','https://pixabay.com/photos/crackers-fire-holiday-celebration-2803438/','Tanuj_handa'),
 'rupee': ('rupee.jpg','05004cc04552201f0e93043171018a5be213a465b1e501fe9b08ddfdc8e9070a','https://pixabay.com/photos/rupee-indian-india-money-bank-note-4395462/','rupixen'),
 'medical_books': ('medical_books.jpg','879edd37ddaa371cc59dcbdb2803110e678d0c8e89c1d04af370e0a216e73088','https://pixabay.com/photos/books-library-bookshelf-1920341/','Pixabay contributor'),
}
# Stock imagery cannot be presented as a depiction of crime, disaster or an
# individual event. Those stories require a specifically reviewed image.
RISK = re.compile(r'\b(?:accident|blast|explosion|explodes?|fire breaks|fire kills|killed|dead|deaths?|died|injur\w*|victims?|arrest\w*|seiz\w*|seized|raids?|fraud|scam|theft|stolen|robber\w*|counterfeit|fake|suicide|protest\w*|riot\w*)\b',re.I)

def select_curated_photo(headline: str, english_headline: str, article: str = "") -> list[dict]:
    text = ' '.join((headline or '', english_headline or ''))
    # A real landmark is relevant to a Paraguayan presidential political
    # story, but never present it as a photo of the protest or other event.
    if re.search(r'\bparaguay\b', text, re.I) and re.search(r'\b(?:president|presidential|pena|peña|impeach\w*)\b', text + ' ' + article[:1400], re.I):
        kind='paraguay_presidency'
    elif RISK.search(text) or RISK.search(article): return []
    elif re.search(r'\bfirecrackers?\b', text, re.I) or (re.search(r'\bcrackers?\b', text, re.I) and re.search(r'\b(?:fireworks?|supreme court|pollution|green)\b', text, re.I)):
        kind='firecrackers'
    elif re.search(r'\b(?:rupee|rupees)\b',text,re.I) and re.search(r'\b(?:exchange|currency|dollar|forex)\b',text,re.I):
        kind='rupee'
    elif re.search(r'\b(?:medical college|medical education|medical admission|neet)\b',text,re.I):
        kind='medical_books'
    else: return []
    filename,digest,url,credit=PHOTOS[kind];path=ASSETS/filename
    if not path.is_file() or sha256_file(path)!=digest:return []
    return [{'url':url,'source_page':url,'license_ref':LICENSE,'credit':credit,
             'local_path':str(path),'sha256':digest,'provider':'pixabay',
             'relevance_reviewed_by':'curated editorial policy',
             'rights_reviewed_by':'curated Pixabay license review',
             'relevance_note':'Topical illustrative stock; not a photograph of the reported event.',
             'illustrative':True,'curated_topic':kind,
             'caption_disclosure': ('File photo: Paraguay’s presidential palace; not a photo of the reported event.' if kind == 'paraguay_presidency' else 'Photo: illustrative stock image, not a photograph of the reported event.') }]
