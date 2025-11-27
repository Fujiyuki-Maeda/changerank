import pandas as pd
import re
import json
from datetime import datetime
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Sum
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from .models import Category, Shop, SalesRecord
from .forms import ExcelUploadForm

# --- ヘルパー関数 ---
def get_descendant_category_ids(dept10_code):
    try:
        root = Category.objects.get(code=dept10_code, level=10)
    except Category.DoesNotExist:
        return []
    ids = {root.id}
    l35 = Category.objects.filter(parent=root)
    ids.update(l35.values_list('id', flat=True))
    l90 = Category.objects.filter(parent__in=l35)
    ids.update(l90.values_list('id', flat=True))
    l180 = Category.objects.filter(parent__in=l90)
    ids.update(l180.values_list('id', flat=True))
    return list(ids)

def upload_category_master(request):
    """
    部門マスタ(10-35-90-180階層)の一括登録
    """
    if request.method == 'POST':
        form = ExcelUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                df = pd.read_excel(request.FILES['file'], header=None, engine='openpyxl')
                
                for index, row in df.iloc[1:].iterrows():
                    if pd.isna(row[0]): continue

                    # 10部門
                    code_10 = int(row[0])
                    name_10 = str(row[1])
                    cat_10, _ = Category.objects.get_or_create(code=code_10, level=10, defaults={'name': name_10})
                    if cat_10.name != name_10:
                        cat_10.name = name_10
                        cat_10.save()

                    # 35部門
                    code_35 = int(row[2])
                    name_35 = str(row[3])
                    cat_35, _ = Category.objects.get_or_create(code=code_35, level=35, defaults={'name': name_35, 'parent': cat_10})
                    if cat_35.name != name_35 or cat_35.parent != cat_10:
                        cat_35.name = name_35
                        cat_35.parent = cat_10
                        cat_35.save()

                    # 90部門
                    code_90 = int(row[4])
                    name_90 = str(row[5])
                    cat_90, _ = Category.objects.get_or_create(code=code_90, level=90, defaults={'name': name_90, 'parent': cat_35})
                    if cat_90.name != name_90 or cat_90.parent != cat_35:
                        cat_90.name = name_90
                        cat_90.parent = cat_35
                        cat_90.save()

                    # 180部門
                    code_180 = int(row[6])
                    name_180 = str(row[7])
                    cat_180, _ = Category.objects.get_or_create(code=code_180, level=180, defaults={'name': name_180, 'parent': cat_90})
                    if cat_180.name != name_180 or cat_180.parent != cat_90:
                        cat_180.name = name_180
                        cat_180.parent = cat_90
                        cat_180.save()

                # ★データ更新時はキャッシュをクリア
                cache.clear()
                messages.success(request, "部門マスタの取り込みが完了しました！")
                return redirect('admin:index')

            except Exception as e:
                messages.error(request, f"エラーが発生しました: {e}")
    else:
        form = ExcelUploadForm()

    return render(request, 'admin/master_upload.html', {'form': form, 'title': '部門マスタ取込'})


def upload_sales_data(request):
    """
    売上実績データ取込
    ピンポイント垂直探索版（原価率スキップ機能付き）
    """
    if request.method == 'POST':
        form = ExcelUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                excel_file = request.FILES['file']
                engine = 'openpyxl'
                if excel_file.name.endswith('.xls'): engine = 'xlrd'
                
                # 1. シート検索
                try:
                    xl = pd.ExcelFile(excel_file, engine=engine)
                    sheet_names = xl.sheet_names
                    target_sheet = None
                    
                    # 優先1: "類" を含むシート
                    for name in sheet_names:
                        if ("180" in name or "１８０" in name) and "明細" in name and "類" in name:
                            target_sheet = name
                            break
                    # 優先2: なければ "180" と "明細" を含むシート
                    if not target_sheet:
                        for name in sheet_names:
                            if ("180" in name or "１８０" in name) and "明細" in name:
                                target_sheet = name
                                break
                    # 優先3: それでもなければ1番目のシート
                    if not target_sheet:
                        target_sheet = sheet_names[0]
                    
                    df = pd.read_excel(excel_file, sheet_name=target_sheet, header=None, engine=engine)
                except:
                    df = pd.read_excel(excel_file, header=None, engine=engine)

                # 2. 日付検索
                report_date = None
                search_limit = min(20, len(df))
                for r in range(search_limit):
                    for c in range(len(df.columns)):
                        val = df.iloc[r, c]
                        if isinstance(val, datetime): report_date = val.date(); break
                        val_str = str(val)
                        match = re.search(r'(\d+)年(\d+)月(\d+)日', val_str)
                        if match: y, m, d = map(int, match.groups()); report_date = datetime(y, m, d).date(); break
                    if report_date: break
                if not report_date: raise ValueError("日付が見つかりませんでした。")

                # 3. 「販売」行の特定
                sales_header_row = -1
                for r in range(search_limit):
                    row_values = [str(v) for v in df.iloc[r].values]
                    if any("販売" in v for v in row_values): sales_header_row = r; break
                if sales_header_row == -1: raise ValueError("列名「販売」が見つかりませんでした。")

                # 誤検知防止リスト
                exclude_keywords = ["原価率", "累計", "合計", "構成比", "予算", "前年", "売上", "仕入", "販売"]
                exclude_names = set(Category.objects.filter(level=10).values_list('name', flat=True))
                
                shop_columns = []
                for col_idx in range(len(df.columns)):
                    val = str(df.iloc[sales_header_row, col_idx])
                    if "販売" in val:
                        found_shop_name = ""
                        for offset in range(1, 7):
                            target_row = sales_header_row - offset
                            if target_row < 0: break
                            cell_val = df.iloc[target_row, col_idx]
                            val_str = str(cell_val).strip()
                            if pd.isna(cell_val) or val_str == "" or val_str == "nan": continue
                            if any(k in val_str for k in exclude_keywords): continue
                            if "%" in val_str: continue
                            if isinstance(cell_val, (int, float)): continue
                            if val_str in exclude_names: continue
                            found_shop_name = val_str; break
                        if found_shop_name:
                            shop_obj, _ = Shop.objects.get_or_create(name=found_shop_name)
                            shop_columns.append({'shop': shop_obj, 'col_idx': col_idx})
                if not shop_columns: raise ValueError(f"店舗情報が見つかりませんでした。(販売行:{sales_header_row+1} の上を確認しました)")

                # 4. データ登録
                count = 0
                data_start_row = sales_header_row + 1
                for index, row in df.iloc[data_start_row:].iterrows():
                    code_val = row[0]
                    if pd.isna(code_val) or not str(code_val).isdigit(): continue
                    try: category_obj = Category.objects.get(code=int(code_val), level=180)
                    except Category.DoesNotExist: continue
                    
                    for shop_info in shop_columns:
                        shop = shop_info['shop']
                        col_idx = shop_info['col_idx']
                        
                        # 売上
                        sales_val = row[col_idx]
                        if pd.isna(sales_val): sales_val = 0
                        try: sales_val = int(sales_val)
                        except: sales_val = 0
                        
                        # 粗利
                        profit_idx = col_idx + 4
                        profit_val = 0
                        if profit_idx < len(row):
                            p_val = row[profit_idx]
                            if not pd.isna(p_val):
                                try: profit_val = int(p_val)
                                except: profit_val = 0

                        SalesRecord.objects.update_or_create(
                            shop=shop,
                            category=category_obj,
                            date=report_date,
                            defaults={
                                'amount_sales': sales_val,
                                'amount_profit': profit_val
                            }
                        )
                    count += 1
                
                # ★データ更新時はキャッシュをクリア
                cache.clear()
                messages.success(request, f"{report_date} のデータ取り込み完了！(売上・粗利 / {count}行処理)")
                return redirect('admin:index')

            except Exception as e:
                import traceback; print(traceback.format_exc()); messages.error(request, f"エラー: {e}")
    else: form = ExcelUploadForm()
    return render(request, 'admin/sales_upload.html', {'form': form, 'title': '売上データ取込'})

# ★キャッシュ有効化 (1日キャッシュ)
@cache_page(60 * 60 * 24)
def student_dashboard(request):
    """生徒用ダッシュボード（部門ランキング）"""
    all_dates = SalesRecord.objects.dates('date', 'day')
    years = sorted(list(set([d.year for d in all_dates])))
    if not years: return render(request, 'dashboard.html', {'error': 'データがまだありません。'})
    
    # ドリルダウン処理
    parent_id = request.GET.get('parent_id')
    current_level = 10
    parent_category = None
    target_categories = []
    breadcrumbs = []

    if parent_id:
        try:
            parent_category = Category.objects.get(id=parent_id)
            current_level = parent_category.level
            curr = parent_category
            while curr:
                breadcrumbs.insert(0, curr)
                curr = curr.parent
            
            if current_level == 10:
                target_categories = Category.objects.filter(parent=parent_category).order_by('code')
            else:
                target_categories = [parent_category]
        except Category.DoesNotExist: pass
    
    if not target_categories:
        target_categories = Category.objects.filter(level=10).order_by('code')
        current_level = 10

    # --- 高速化: マッピング作成 ---
    cat_map = {}
    for target in target_categories:
        descendants = []
        if target.level == 10: descendants = Category.objects.filter(parent__parent__parent=target, level=180)
        elif target.level == 35: descendants = Category.objects.filter(parent__parent=target, level=180)
        elif target.level == 90: descendants = Category.objects.filter(parent=target, level=180)
        elif target.level == 180: descendants = [target]
        for d in descendants: cat_map[d.id] = target.id

    year_data = {}
    relevant_ids = list(cat_map.keys())
    for year in years:
        latest_date = SalesRecord.objects.filter(date__year=year).order_by('-date').values_list('date', flat=True).first()
        if not latest_date: continue
        
        records = SalesRecord.objects.filter(date=latest_date, category_id__in=relevant_ids).values('category_id', 'amount_sales')
        
        dept_totals = {}
        for r in records:
            cat_id = r['category_id']
            amount = r['amount_sales']
            target_id = cat_map.get(cat_id)
            if target_id:
                dept_totals[target_id] = dept_totals.get(target_id, 0) + amount
        
        total_sales = sum(dept_totals.values())
        sorted_depts = sorted(dept_totals.items(), key=lambda x: x[1], reverse=True)
        
        year_info = {}
        for rank, (c_id, amount) in enumerate(sorted_depts, 1):
            share = round((amount / total_sales * 100), 1) if total_sales > 0 else 0
            year_info[c_id] = {'rank': rank, 'share': share}
        year_data[year] = year_info

    table_data = []
    for dept in target_categories:
        row = {
            'id': dept.id,
            'name': dept.name,
            'level': dept.level,
            'is_clickable': dept.level < 35,
            'cells': []
        }
        for i, year in enumerate(years):
            data = year_data.get(year, {}).get(dept.id, None)
            rank = data['rank'] if data else None
            share = data['share'] if data else None
            row['cells'].append({'year': year, 'rank': rank, 'share': share})
        table_data.append(row)
    
    latest_year = years[-1]
    table_data.sort(key=lambda x: year_data.get(latest_year, {}).get(x['id'], {}).get('rank', 999))
    
    context = {
        'years': years, 
        'table_data': table_data,
        'breadcrumbs': breadcrumbs,
        'current_title': parent_category.name if parent_category else "全社（10部門）",
    }
    return render(request, 'dashboard.html', context)

@cache_page(60 * 60 * 24)
def trend_dashboard(request):
    dates = SalesRecord.objects.dates('date', 'day').order_by('date')
    if not dates: return render(request, 'trend_dashboard.html', {'error': 'データがありません。'})
    labels = [d.strftime('%Y/%m/%d') for d in dates]
    
    cat_map = {}
    l10s = Category.objects.filter(level=10)
    for l10 in l10s:
        descendants = Category.objects.filter(parent__parent__parent=l10, level=180).values_list('id', flat=True)
        for d_id in descendants: cat_map[d_id] = l10.name
    
    categories_10 = l10s.order_by('code')
    dataset_map = {cat.name: [0] * len(dates) for cat in categories_10}
    
    for i, d in enumerate(dates):
        records = SalesRecord.objects.filter(date=d).values('category_id', 'amount_sales')
        daily_totals = {cat.name: 0 for cat in categories_10}
        for r in records:
            cat_id = r['category_id']; amount = r['amount_sales']; root_name = cat_map.get(cat_id)
            if root_name and root_name in daily_totals: daily_totals[root_name] += amount
        total_sales = sum(daily_totals.values())
        if total_sales > 0:
            for name, amount in daily_totals.items(): dataset_map[name][i] = round((amount / total_sales) * 100, 1)
            
    colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#E7E9ED', '#71B37C', '#EC932F', '#5D6D7E']
    datasets = []
    for i, (name, data) in enumerate(dataset_map.items()):
        datasets.append({'label': name, 'data': data, 'borderColor': colors[i % len(colors)], 'backgroundColor': colors[i % len(colors)], 'fill': False, 'tension': 0.1})
    context = {'labels': json.dumps(labels), 'datasets': json.dumps(datasets)}
    return render(request, 'trend_dashboard.html', context)

@cache_page(60 * 60 * 24)
def shop_ranking(request):
    all_dates = SalesRecord.objects.dates('date', 'day')
    years = sorted(list(set([d.year for d in all_dates])))
    if not years: return render(request, 'shop_ranking.html', {'error': 'データがまだありません。'})
    
    checkbox_shops = Shop.objects.exclude(name="加治").order_by('name')
    selected_shop_ids = request.GET.getlist('selected_shops')
    if selected_shop_ids: selected_shop_ids = [int(x) for x in selected_shop_ids if x.isdigit()]
    
    all_10_depts = Category.objects.filter(level=10).order_by('code')
    selected_dept_code = request.GET.get('dept_code')
    selected_dept_name = "全店合計"
    
    target_category_ids = []
    if selected_dept_code:
        target_category_ids = get_descendant_category_ids(selected_dept_code)
        try: selected_dept_name = Category.objects.get(code=selected_dept_code, level=10).name
        except: pass
    
    year_data = {}
    for year in years:
        latest_date = SalesRecord.objects.filter(date__year=year).order_by('-date').values_list('date', flat=True).first()
        if not latest_date: continue
        
        qs = SalesRecord.objects.filter(date=latest_date)
        if target_category_ids: qs = qs.filter(category__id__in=target_category_ids)
        
        records = qs.values('shop__name', 'amount_sales')
        shop_totals = {}
        for d in records:
            shop_name = d['shop__name']; amount = d['amount_sales']
            if shop_name == "加治": shop_name = "加治木"
            shop_totals[shop_name] = shop_totals.get(shop_name, 0) + amount
        
        sorted_shops = sorted(shop_totals.items(), key=lambda x: x[1], reverse=True)
        year_info = {}
        for rank, (name, amount) in enumerate(sorted_shops, 1): year_info[name] = {'rank': rank}
        year_data[year] = year_info

    all_shops = Shop.objects.all().order_by('name')
    table_data = []
    for shop in all_shops:
        if shop.name == "加治": continue
        if selected_shop_ids and shop.id not in selected_shop_ids: continue
        
        row = {'name': shop.name, 'cells': []}
        has_data = False
        for i, year in enumerate(years):
            data = year_data.get(year, {}).get(shop.name, None)
            if data: has_data = True
            rank = data['rank'] if data else None
            diff_icon = ""; diff_class = ""; status_text = ""
            
            if i > 0:
                prev_year = years[i-1]; prev_data = year_data.get(prev_year, {}).get(shop.name, None); prev_rank = prev_data['rank'] if prev_data else None
                if rank and not prev_rank: status_text = "新店"; diff_class = "store-new"
                elif not rank and prev_rank: status_text = "閉店"; diff_class = "store-closed"
                elif rank and prev_rank:
                    if rank < prev_rank: diff = prev_rank - rank; diff_icon = f"↑{diff}"; diff_class = "rank-up"
                    elif rank > prev_rank: diff = rank - prev_rank; diff_icon = f"↓{diff}"; diff_class = "rank-down"
                    else: diff_icon = "→"; diff_class = "rank-same"
            row['cells'].append({'year': year, 'rank': rank, 'diff_icon': diff_icon, 'diff_class': diff_class, 'status_text': status_text})
        
        if has_data: table_data.append(row)
    
    latest_year = years[-1]
    table_data.sort(key=lambda x: year_data.get(latest_year, {}).get(x['name'], {}).get('rank', 999))
    
    context = {
        'years': years, 'table_data': table_data, 
        'checkbox_shops': checkbox_shops, 'selected_shop_ids': selected_shop_ids,
        'all_10_depts': all_10_depts, 'selected_dept_code': selected_dept_code, 'selected_dept_name': selected_dept_name
    }
    return render(request, 'shop_ranking.html', context)

@cache_page(60 * 60 * 24)
def profit_ranking(request):
    """粗利率ランキング（年表形式・10年分）"""
    all_dates = SalesRecord.objects.dates('date', 'day')
    years = sorted(list(set([d.year for d in all_dates])))
    if not years: return render(request, 'profit_ranking.html', {'error': 'データがまだありません。'})
    
    cat_map = {}
    l10s = Category.objects.filter(level=10)
    for l10 in l10s:
        descendants = Category.objects.filter(parent__parent__parent=l10, level=180).values_list('id', flat=True)
        for d_id in descendants: cat_map[d_id] = l10.name
    
    year_data = {}
    for year in years:
        latest_date = SalesRecord.objects.filter(date__year=year).order_by('-date').values_list('date', flat=True).first()
        if not latest_date: continue
        
        records = SalesRecord.objects.filter(date=latest_date).values('category_id', 'amount_sales', 'amount_profit')
        dept_data = {}
        for r in records:
            cat_id = r['category_id']; root_name = cat_map.get(cat_id)
            if root_name:
                if root_name not in dept_data: dept_data[root_name] = {'sales': 0, 'profit': 0}
                dept_data[root_name]['sales'] += r['amount_sales']
                dept_data[root_name]['profit'] += r['amount_profit']
        
        sorted_by_sales = sorted(dept_data.items(), key=lambda x: x[1]['sales'], reverse=True)
        sales_ranks = {name: i+1 for i, (name, _) in enumerate(sorted_by_sales)}
        
        margin_list = []
        for name, data in dept_data.items():
            sales = data['sales']; profit = data['profit']
            margin = (profit / sales * 100) if sales > 0 else 0
            margin_list.append((name, margin))
        
        sorted_by_margin = sorted(margin_list, key=lambda x: x[1], reverse=True)
        
        year_info = {}
        for i, (name, margin) in enumerate(sorted_by_margin):
            profit_rank = i + 1; sales_rank = sales_ranks.get(name, 0); gap = sales_rank - profit_rank
            year_info[name] = {
                'profit_rank': profit_rank, 'sales_rank': sales_rank,
                'gap': gap, 'gap_abs': abs(gap), 'margin': round(margin, 1)
            }
        year_data[year] = year_info

    all_depts = Category.objects.filter(level=10).order_by('code')
    table_data = []
    for dept in all_depts:
        row = {'name': dept.name, 'cells': []}
        for i, year in enumerate(years):
            data = year_data.get(year, {}).get(dept.name, None)
            profit_rank = data['profit_rank'] if data else None
            gap = data['gap'] if data else 0
            gap_abs = data['gap_abs'] if data else 0
            sales_rank = data['sales_rank'] if data else None
            margin = data['margin'] if data else None
            
            gap_class = ""; gap_icon = ""
            if profit_rank:
                if gap > 0: gap_class = "gap-up"; gap_icon = f"売{sales_rank}位 ↗"
                elif gap < 0: gap_class = "gap-down"; gap_icon = f"売{sales_rank}位 ↘"
                else: gap_class = "gap-same"; gap_icon = "-"
            
            row['cells'].append({'year': year, 'rank': profit_rank, 'gap_class': gap_class, 'gap_icon': gap_icon, 'gap_abs': gap_abs, 'margin': margin})
        table_data.append(row)

    latest_year = years[-1]
    table_data.sort(key=lambda x: year_data.get(latest_year, {}).get(x['name'], {}).get('profit_rank', 999))

    context = {'years': years, 'table_data': table_data}
    return render(request, 'profit_ranking.html', context)

@cache_page(60 * 60 * 24)
def profit_map(request):
    all_dates = SalesRecord.objects.dates('date', 'day', order='DESC')
    if not all_dates: return render(request, 'profit_map.html', {'error': 'データがまだありません。'})
    date_param = request.GET.get('date')
    target_date = None
    if date_param:
        try: target_date = datetime.strptime(date_param, '%Y-%m-%d').date()
        except ValueError: target_date = None
    if not target_date: target_date = all_dates[0]

    cat_map = {}
    l10s = Category.objects.filter(level=10)
    for l10 in l10s:
        descendants = Category.objects.filter(parent__parent__parent=l10, level=180).values_list('id', flat=True)
        for d_id in descendants: cat_map[d_id] = l10.name

    records = SalesRecord.objects.filter(date=target_date).values('category_id', 'amount_sales', 'amount_profit')
    dept_data = {}
    for r in records:
        cat_id = r['category_id']; root_name = cat_map.get(cat_id)
        if root_name:
            if root_name not in dept_data: dept_data[root_name] = {'sales': 0, 'profit': 0}
            dept_data[root_name]['sales'] += r['amount_sales']
            dept_data[root_name]['profit'] += r['amount_profit']

    colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#E7E9ED', '#71B37C', '#EC932F', '#5D6D7E']
    scatter_data = []
    for i, (name, data) in enumerate(dept_data.items()):
        sales = data['sales']; profit = data['profit']; margin = round((profit / sales * 100), 2) if sales > 0 else 0
        color = colors[i % len(colors)]
        scatter_data.append({'x': sales, 'y': margin, 'label': name, 'profit': profit, 'color': color})
    context = {'date': target_date, 'available_dates': all_dates, 'scatter_data': json.dumps(scatter_data)}
    return render(request, 'profit_map.html', context)

# ★新規追加: 日向店の10年分売上構成比推移
@cache_page(60 * 60 * 24)
def hyuga_trend(request):
    """
    日向店の売上構成比（10部門）の推移を積み上げ棒グラフで表示する
    """
    # 日付データの取得
    dates = SalesRecord.objects.dates('date', 'day').order_by('date')
    if not dates:
        return render(request, 'hyuga_trend.html', {'error': 'データがまだありません。'})
        
    # "日向" を含む店舗を探す
    hyuga_shop = Shop.objects.filter(name__contains="日向").first()
    if not hyuga_shop:
        return render(request, 'hyuga_trend.html', {'error': '「日向」という名前の店舗が見つかりませんでした。'})

    # グラフのラベル（年）
    labels = [d.strftime('%Y') for d in dates]

    # 10部門の準備
    l10s = Category.objects.filter(level=10).order_by('code')
    
    # マッピング作成: {180部門ID: 10部門名}
    cat_map = {}
    for l10 in l10s:
        descendants = Category.objects.filter(parent__parent__parent=l10, level=180).values_list('id', flat=True)
        for d_id in descendants:
            cat_map[d_id] = l10.name
            
    # データセットの初期化: { '部門名': [2016シェア, 2017シェア...] }
    dataset_map = {cat.name: [0] * len(dates) for cat in l10s}
    
    for i, d in enumerate(dates):
        # その年の日向店のデータを取得
        records = SalesRecord.objects.filter(date=d, shop=hyuga_shop).values('category_id', 'amount_sales')
        
        daily_totals = {cat.name: 0 for cat in l10s}
        
        for r in records:
            cat_id = r['category_id']
            amount = r['amount_sales']
            root_name = cat_map.get(cat_id)
            if root_name:
                daily_totals[root_name] += amount
        
        total_sales = sum(daily_totals.values())
        
        if total_sales > 0:
            for name, amount in daily_totals.items():
                share = (amount / total_sales) * 100
                dataset_map[name][i] = round(share, 1)
    
    # Chart.js用データ作成
    colors = [
        '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', 
        '#FF9F40', '#E7E9ED', '#71B37C', '#EC932F', '#5D6D7E'
    ]
    
    datasets = []
    for i, (name, data) in enumerate(dataset_map.items()):
        datasets.append({
            'label': name,
            'data': data,
            'backgroundColor': colors[i % len(colors)],
        })

    context = {
        'shop_name': hyuga_shop.name,
        'labels': json.dumps(labels),
        'datasets': json.dumps(datasets),
    }
    return render(request, 'hyuga_trend.html', context)

@cache_page(60 * 60 * 24)
def store_comparison(request):
    all_dates = SalesRecord.objects.dates('date', 'day')
    years = sorted(list(set([d.year for d in all_dates])), reverse=True)
    if not years: return render(request, 'store_comparison.html', {'error': 'データがまだありません。'})
    all_shops = Shop.objects.exclude(name="加治").order_by('name')
    selected_year = request.GET.get('year')
    if selected_year: selected_year = int(selected_year)
    else: selected_year = years[0]
    target_shop_id = request.GET.get('target_shop')
    comparison_shop_ids = request.GET.getlist('comparison_shops')
    if target_shop_id: target_shop_id = int(target_shop_id)
    comparison_shop_ids = [int(x) for x in comparison_shop_ids if x.isdigit()]
    cat_map = {}
    l10s = Category.objects.filter(level=10).order_by('code')
    dept_names = [c.name for c in l10s]
    for l10 in l10s:
        descendants = Category.objects.filter(parent__parent__parent=l10, level=180).values_list('id', flat=True)
        for d_id in descendants: cat_map[d_id] = l10.name
    chart_data = {'labels': dept_names, 'datasets': []}
    selected_ids = []
    if target_shop_id: selected_ids.append(target_shop_id)
    selected_ids.extend(comparison_shop_ids)
    if selected_ids:
        latest_date = SalesRecord.objects.filter(date__year=selected_year).order_by('-date').values_list('date', flat=True).first()
        if latest_date:
            records = SalesRecord.objects.filter(date=latest_date, shop_id__in=selected_ids).values('shop_id', 'shop__name', 'category_id', 'amount_sales')
            shop_aggs = {}
            for r in records:
                sid = r['shop_id']; sname = r['shop__name']
                if sname == "加治": sname = "加治木"
                if sid not in shop_aggs: shop_aggs[sid] = {'name': sname, 'total': 0, 'depts': {d: 0 for d in dept_names}}
                cat_id = r['category_id']; root_name = cat_map.get(cat_id)
                if root_name:
                    shop_aggs[sid]['depts'][root_name] += r['amount_sales']
                    shop_aggs[sid]['total'] += r['amount_sales']
            if target_shop_id and target_shop_id in shop_aggs:
                data = shop_aggs[target_shop_id]
                shares = [round((data['depts'][d] / data['total'] * 100), 1) if data['total'] > 0 else 0 for d in dept_names]
                chart_data['datasets'].append({'label': data['name'], 'data': shares, 'backgroundColor': 'rgba(255, 99, 132, 0.7)', 'borderColor': 'rgba(255, 99, 132, 1)', 'borderWidth': 1})
            colors = ['#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40']
            c_idx = 0
            for sid in comparison_shop_ids:
                if sid in shop_aggs:
                    data = shop_aggs[sid]
                    shares = [round((data['depts'][d] / data['total'] * 100), 1) if data['total'] > 0 else 0 for d in dept_names]
                    chart_data['datasets'].append({'label': data['name'], 'data': shares, 'backgroundColor': colors[c_idx % len(colors)], 'borderColor': colors[c_idx % len(colors)], 'borderWidth': 1})
                    c_idx += 1
    context = {'years': years, 'all_shops': all_shops, 'selected_year': selected_year, 'target_shop_id': target_shop_id, 'comparison_shop_ids': comparison_shop_ids, 'chart_data': json.dumps(chart_data)}
    return render(request, 'store_comparison.html', context)