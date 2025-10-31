import json, os
from pprint import pprint
import copy
from super_buyer.config import DEFAULT_CONFIG, load_config
# Load current config
with open('config.json','r',encoding='utf-8') as f:
    cfg = json.load(f)
# Build defaults

defc = copy.deepcopy(DEFAULT_CONFIG)
# Simulate load_config behavior to add tolerant defaults
cfg_loaded = load_config('config.json')

def get(d, path, default=None):
    cur=d
    for k in path.split('.'):
        if not isinstance(cur, dict):
            return default
        cur=cur.get(k, default)
    return cur

print('--- Templates extras (in cfg but not in defaults) ---')
extra_tpl = set(get(cfg_loaded,'templates',{}).keys()) - set(defc['templates'].keys())
print(sorted(extra_tpl))
print('\n--- Templates missing (in defaults but not in cfg) ---')
miss_tpl = set(defc['templates'].keys()) - set(get(cfg_loaded,'templates',{}).keys())
print(sorted(miss_tpl))
print('\n--- Templates differing confidence (cfg vs default) ---')
diff_conf={}
for k in sorted(set(defc['templates']).intersection(get(cfg_loaded,'templates',{}))):
    dc=defc['templates'][k].get('confidence')
    cc=get(cfg_loaded,'templates',{})[k].get('confidence')
    if dc!=cc:
        diff_conf[k]=(dc,cc)
print(diff_conf)

print('\n--- price_roi defaults vs cfg_loaded ---')
pr_def=defc.get('price_roi',{})
pr_cfg=get(cfg_loaded,'price_roi',{})
for k in sorted(set(list(pr_def.keys())+list(pr_cfg.keys()))):
    dv=pr_def.get(k)
    cv=pr_cfg.get(k)
    if dv!=cv:
        print(f'{k}: default={dv!r}, cfg={cv!r}')

print('\n--- avg_price_area defaults vs cfg_loaded ---')
ap_def=defc.get('avg_price_area',{})
ap_cfg=get(cfg_loaded,'avg_price_area',{})
for k in sorted(set(list(ap_def.keys())+list(ap_cfg.keys()))):
    dv=ap_def.get(k)
    cv=ap_cfg.get(k)
    if dv!=cv:
        print(f'{k}: default={dv!r}, cfg={cv!r}')

print('\n--- game defaults vs cfg_loaded ---')
for k in sorted(set(list(defc['game'].keys())+list(cfg_loaded.get('game',{}).keys()))):
    dv=defc['game'].get(k)
    cv=cfg_loaded.get('game',{}).get(k)
    if dv!=cv:
        print(f'{k}: default={dv!r}, cfg={cv!r}')

print('\n--- hotkeys defaults vs cfg_loaded ---')
for k in sorted(set(list(defc['hotkeys'].keys())+list(cfg_loaded.get('hotkeys',{}).keys()))):
    dv=defc['hotkeys'].get(k)
    cv=cfg_loaded.get('hotkeys',{}).get(k)
    if dv!=cv:
        print(f'{k}: default={dv!r}, cfg={cv!r}')

print('\n--- umi_ocr defaults vs cfg_loaded ---')
for k in sorted(set(list(defc['umi_ocr'].keys())+list(cfg_loaded.get('umi_ocr',{}).keys()))):
    dv=defc['umi_ocr'].get(k)
    cv=cfg_loaded.get('umi_ocr',{}).get(k)
    if dv!=cv:
        print(f'{k}: default={dv!r}, cfg={cv!r}')

print('\n--- extra top-level keys present in cfg but not defaults ---')
print(sorted(set(cfg_loaded.keys())-set(defc.keys())))
