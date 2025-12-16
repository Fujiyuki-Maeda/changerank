from change.views import get_all_dates_cached
from change.models import SalesRecord

years = sorted(list({d.year for d in get_all_dates_cached()}), reverse=True)
print('years', years)
chosen = None
for y in years:
    if SalesRecord.objects.filter(date__year=y).exclude(category__code=9999).exists():
        chosen = y
        break
print('chosen', chosen)
print('selected_year (view logic):', chosen if chosen is not None else years[0])
