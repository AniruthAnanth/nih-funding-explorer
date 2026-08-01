"""Does an UNBIASED per-author affiliation profile predict NIH's department?

The production harvest queried PubMed for institution AND surgery, so it cannot
distinguish "is in surgery" from "appeared on a surgery paper". This samples
contact PIs at institutions NIH *does* department-code, pulls every paper for
that author-institution with no surgery filter, takes the dominant department
across their affiliation strings, and scores it against NIH's own field.
"""
import sys, json, time, random, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
sys.path.insert(0,'/Users/cunningviper/Documents/yang/RankMGB-UrgentRequest/src')
import pandas as pd, numpy as np
from rankmgb.mgb_surgery import NARROW, _patterns, classify_near_institution
from rankmgb.pubmed_evidence import INSTITUTION_PATTERNS, _REG

EUT="https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
def get(ep, params, retries=4):
    url=f"{EUT}/{ep}?{urllib.parse.urlencode({**params,'tool':'RankMGB','email':'aniruth@stanford.edu'})}"
    for a in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r: return r.read()
        except Exception: time.sleep(1.5*(a+1))
    return None

NIH_SURG={"SURGERY","NEUROSURGERY","ORTHOPEDICS","OTOLARYNGOLOGY","UROLOGY","PLASTIC SURGERY"}
qmap=dict(zip(_REG.canonical_org_id,_REG.pubmed_query))

df=pd.read_parquet('data/processed/award_years_annotated.parquet')
pis=pd.read_parquet('data/processed/pi_links.parquet')
pis=pis[pis.is_contact_pi].drop(columns=[c for c in ('fiscal_year','org_ipf_code') if c in pis.columns])
d=pis.merge(df[['application_id','canonical_org_id','nih_org_dept','index_date']],on='application_id')
d=d[d.canonical_org_id.isin(qmap) & ~d.nih_org_dept.isin(['__MISSING__','NONE','MISCELLANEOUS','NO CODE ASSIGNED'])]
# one row per (PI, institution): their modal NIH department
g=(d.groupby(['canonical_org_id','pi_name_raw'])
     .agg(nih_dept=('nih_org_dept', lambda s: s.value_counts().index[0]), n=('application_id','size'))
     .reset_index())
# oversample surgical so the sample has signal on both sides
random.seed(7)
surg=g[g.nih_dept.isin(NIH_SURG)].sample(min(150,len(g[g.nih_dept.isin(NIH_SURG)])),random_state=7)
non =g[~g.nih_dept.isin(NIH_SURG)].sample(150,random_state=7)
samp=pd.concat([surg,non]).reset_index(drop=True)
print(f"sample: {len(samp)} PI-institution pairs ({len(surg)} NIH-surgical, {len(non)} not)", flush=True)

pats=_patterns()
rows=[]
for k,(_,r) in enumerate(samp.iterrows(),1):
    last=str(r.pi_name_raw).split(',')[0].strip()
    fore=(str(r.pi_name_raw).split(',')[1].strip() if ',' in str(r.pi_name_raw) else '')
    ini=(fore[:1] or '')
    if not last or not ini: continue
    term=f'("{last} {ini}"[Author]) AND ({qmap[r.canonical_org_id]}) AND 2020:2026[dp]'
    js=get('esearch',{'db':'pubmed','term':term,'retmax':'120','retmode':'json'})
    if not js: continue
    ids=json.loads(js)['esearchresult'].get('idlist',[])
    if not ids: continue
    xb=get('efetch',{'db':'pubmed','id':','.join(ids),'retmode':'xml'})
    if not xb: continue
    try: root=ET.fromstring(xb)
    except Exception: continue
    rx=INSTITUTION_PATTERNS[r.canonical_org_id]
    specs=[]
    for art in root.iter('PubmedArticle'):
        for au in art.iter('Author'):
            ln=(au.findtext('LastName') or '').upper()
            fn=(au.findtext('ForeName') or '').upper()
            if ln!=last.upper() or (ini and not fn.startswith(ini.upper())): continue
            for aff in au.iter('Affiliation'):
                t=(aff.text or '').strip()
                if not t or not rx.search(t): continue
                sp=classify_near_institution(t,rx,pats)[0]
                if sp: specs.append(sp)
    if not specs: continue
    s=pd.Series(specs)
    share=s.isin(NARROW).mean()
    rows.append({'org':r.canonical_org_id,'pi':r.pi_name_raw,'nih_dept':r.nih_dept,
                 'nih_surg':r.nih_dept in NIH_SURG,'n_aff':len(specs),
                 'surg_share':share,'modal':s.value_counts().index[0]})
    if k%40==0: print(f"  {k}/{len(samp)} ... {len(rows)} with evidence", flush=True)
    time.sleep(0.34)

out=pd.DataFrame(rows)
out.to_csv('/private/tmp/claude-501/-Users-cunningviper-Documents-yang-RankMGB-UrgentRequest/6d1703ca-626a-4274-928a-dd20c166a3e8/scratchpad/method/validation.csv',index=False)
print(f"\nresolved {len(out)} of {len(samp)} sampled PIs\n", flush=True)

def kappa(a,b):
    po=(a==b).mean(); pa,pb=a.mean(),b.mean(); pe=pa*pb+(1-pa)*(1-pb)
    return (po-pe)/(1-pe) if pe<1 else float('nan')
print(f"{'rule':<34}{'n':>6}{'sens%':>8}{'prec%':>8}{'kappa':>8}")
for name,pred in [
  ("ANY surgical paper", out.surg_share>0),
  ("modal dept is surgical", out.modal.isin(NARROW)),
  ("surgical share > 50%", out.surg_share>0.5),
  ("surgical share > 70%", out.surg_share>0.7),
  ("share>50% and >=3 records", (out.surg_share>0.5)&(out.n_aff>=3)),
]:
    tp=int((out.nih_surg&pred).sum()); fn=int((out.nih_surg&~pred).sum()); fp=int((~out.nih_surg&pred).sum())
    sens=100*tp/(tp+fn) if tp+fn else float('nan')
    prec=100*tp/(tp+fp) if tp+fp else float('nan')
    print(f"{name:<34}{len(out):>6}{sens:>8.1f}{prec:>8.1f}{kappa(out.nih_surg.values,pred.values):>8.3f}")
