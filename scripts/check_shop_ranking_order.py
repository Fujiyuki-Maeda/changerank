from django.test import Client
from django.core.cache import cache

cache.clear()
print('cache cleared')

c = Client()
resp = c.get('/shops/?year=2025')
print('status', resp.status_code)
# get first table row names from rendered HTML
html = resp.content.decode('utf-8')
start = html.find('<table')
if start == -1:
    print('table not found')
else:
    snippet = html[start:start+4000]
    # crude: find first few occurrences of <td class="dept-col">
    parts = snippet.split('<td')
    names = []
    for p in parts:
        if 'dept-col' in p:
            # find > after span
            idx = p.find('>')
            if idx!=-1:
                rest = p[idx+1:idx+200]
                # strip tags
                import re
                text = re.sub('<.*?>','',rest).strip()
                names.append(text)
    print('first dept names:', names[:10])
