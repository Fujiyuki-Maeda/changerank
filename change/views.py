import pandas as pd
import re
import json
from datetime import datetime, date
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Sum, Max
from django.db import transaction
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from .models import Category, Shop, SalesRecord
from .forms import ExcelUploadForm
import threading 
import calendar
import logging
import csv
from django.http import HttpResponse
from collections import OrderedDict

# --- ヘルパー関数 ---

logger = logging.getLogger(__name__)


def build_display_groups(all_shops, raw_tokens=None):
    """Build display grouping for shops.

    Returns: (display_map, all_shops_display, selected_display_values, comparison_shop_ids)
    - display_map: OrderedDict(display_name -> [shop_id,...])
    - all_shops_display: [{'display':name,'value':'id|id', 'ids':[...]}...]
    - selected_display_values: raw tokens for checkbox checked-state
    - comparison_shop_ids: flattened, unique list of int ids parsed from raw_tokens
    """
    def normalize_shop_name(n):
        try:
            return str(n).replace('和歌山', '和歌')
        except Exception:
            return n

    display_map = OrderedDict()
    for s in all_shops:
        disp = normalize_shop_name(s.name)
        display_map.setdefault(disp, []).append(s.id)

    all_shops_display = []
    for disp, ids in display_map.items():
        all_shops_display.append({'display': disp, 'value': '|'.join(map(str, ids)), 'ids': ids})

    # fallback: if grouping produced no entries (unexpected), fall back to raw shop list
    if not all_shops_display:
        for s in all_shops:
            sid = s.id
            sname = s.name if s.name != '加治' else '加治木'
            all_shops_display.append({'display': sname, 'value': str(sid), 'ids': [sid]})

    raw_tokens = raw_tokens or []
    comparison_shop_ids = []
    for token in raw_tokens:
        parts = re.split('[,|]+', token)
        for p in parts:
            if p.isdigit():
                comparison_shop_ids.append(int(p))
    comparison_shop_ids = list(dict.fromkeys(comparison_shop_ids))
    selected_display_values = raw_tokens or []
    return display_map, all_shops_display, selected_display_values, comparison_shop_ids


def normalize_shop_name(n):
    try:
        return str(n).replace('和歌山', '和歌')
    except Exception:
        return n

def get_descendant_category_ids(dept10_code):
    
    # 【修正】キャッシュチェック
    cache_key = f'descendants_{dept10_code}'
    ids = cache.get(cache_key)
    
    if ids is not None:
        return ids
        
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
    
    # 【修正】キャッシュに保存 (1週間)
    ids_list = list(ids)
    cache.set(cache_key, ids_list, 60 * 60 * 24 * 7)
    return ids_list


def get_descendant_ids_for_category(code, level):
    """指定した code と level のカテゴリの id を含め、下位のすべてのカテゴリ id を返す。"""
    cache_key = f'descendants_gen_{level}_{code}'
    ids = cache.get(cache_key)
    if ids is not None:
        return ids
    try:
        root = Category.objects.get(code=code, level=level)
    except Category.DoesNotExist:
        return []
    ids_set = {root.id}
    current = [root]
    # 幅優先で子孫をたどる。children を ids_set に追加してから
    # 次の current を決めると深さ 1 で終わってしまうため、
    # children を取得 -> next_current を準備 -> ids_set を更新、という順にする。
    while current:
        children = list(Category.objects.filter(parent__in=current))
        if not children:
            break
        next_current = []
        for c in children:
            if c.id not in ids_set:
                next_current.append(c)
        # 新たに見つかった子を ids_set に追加し、ループを続ける
        for c in next_current:
            ids_set.add(c.id)
        current = next_current

    # ループ完了後にキャッシュして返す
    ids_list = list(ids_set)
    cache.set(cache_key, ids_list, 60 * 60 * 24 * 7)
    return ids_list


# キャッシュ付きで全日付リストを取得する（.dates() の全表走査を避けるため）
def get_all_dates_cached(ttl=60 * 60 * 24):
    cache_key = 'salesrecord_all_dates'
    dates = cache.get(cache_key)
    if dates is not None:
        return dates
    try:
        qs = SalesRecord.objects.values_list('date', flat=True).distinct().order_by('date')
        dates = list(qs)
        cache.set(cache_key, dates, ttl)
        return dates
    except Exception:
        return []


def get_category_180_groups_cached(ttl=60 * 60 * 24 * 7):
    """180部門ごとに、その上位(90,35,10)の ancestor をまとめて返す。

    返り値: {
        'by_180': {180_id: {'90': id_or_None, '35': id_or_None, '10': id_or_None}},
        'by_90': {90_id: [180_id,...]},
        'by_35': {35_id: [180_id,...]},
        'by_10': {10_id: [180_id,...]},
    }
    キャッシュ化してビュー側の多重クエリを防止する。
    """
    cache_key = 'category_180_groups'
    groups = cache.get(cache_key)
    if groups is not None:
        return groups

    qs = Category.objects.filter(level=180).select_related('parent__parent__parent')
    by_180 = {}
    by_90 = {}
    by_35 = {}
    by_10 = {}

    for c in qs:
        id180 = c.id
        p90 = getattr(c, 'parent', None)
        p35 = getattr(p90, 'parent', None) if p90 else None
        p10 = getattr(p35, 'parent', None) if p35 else None

        by_180[id180] = {'90': p90.id if p90 else None, '35': p35.id if p35 else None, '10': p10.id if p10 else None}

        if p90:
            by_90.setdefault(p90.id, []).append(id180)
        if p35:
            by_35.setdefault(p35.id, []).append(id180)
        if p10:
            by_10.setdefault(p10.id, []).append(id180)

    groups = {'by_180': by_180, 'by_90': by_90, 'by_35': by_35, 'by_10': by_10}
    cache.set(cache_key, groups, ttl)
    return groups

# --- run_sales_aggregation 関数は不使用のため削除 ---
def run_sales_aggregation(report_date):
    pass 

def upload_category_master(request):
    """
    部門マスタ(10-35-90-180階層)の一括登録
    """
    if request.method == 'POST':
        form = ExcelUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                df = pd.read_excel(request.FILES['file'], header=None, engine='openpyxl')
                
                with transaction.atomic():
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

                # データ更新後にキャッシュをクリアする（重要）
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
    """
    if request.method == 'POST':
        form = ExcelUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                # --- 【共通ヘルパー関数をここで定義】 ---
                def get_val(row, col_idx, offset):
                    """指定行、基点列、オフセットから整数値を取得する"""
                    target_idx = col_idx + offset
                    if target_idx < len(row):
                        v = row[target_idx]
                        if pd.isna(v): return 0
                        if isinstance(v, str):
                            v = v.replace(',', '').strip()
                            if v == '' or v == '-': return 0
                        try: return int(float(v))
                        except: return 0
                    return 0
                
                excel_file = request.FILES['file']
                engine = 'openpyxl'
                if excel_file.name.endswith('.xls'): engine = 'xlrd'
                try:
                    xl = pd.ExcelFile(excel_file, engine=engine)
                    sheet_names = xl.sheet_names
                    target_sheet = None
                    for name in sheet_names:
                        if ("180" in name or "１８０" in name) and "明細" in name and "類" in name: target_sheet = name; break
                    if not target_sheet:
                        for name in sheet_names:
                            if ("180" in name or "１８０" in name) and "明細" in name: target_sheet = name; break
                    if not target_sheet: target_sheet = sheet_names[0]
                    df = pd.read_excel(excel_file, sheet_name=target_sheet, header=None, engine=engine)
                except: df = pd.read_excel(excel_file, header=None, engine=engine)

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

                sales_header_row = -1
                for r in range(search_limit):
                    row_values = [str(v) for v in df.iloc[r].values]
                    if any("販売" in v for v in row_values): sales_header_row = r; break
                
                logger.debug(f"sales_header_row index: {sales_header_row}")
                
                if sales_header_row == -1: raise ValueError("列名「販売」が見つかりませんでした。")

                exclude_keywords = ["原価率", "累計", "合計", "構成比", "予算", "前年", "売上", "仕入", "販売"]
                exclude_names = set(Category.objects.filter(level=10).values_list('name', flat=True))
                
                shop_cache = {s.name: s for s in Shop.objects.all()}
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
                            if found_shop_name not in shop_cache:
                                new_shop = Shop.objects.create(name=found_shop_name)
                                shop_cache[found_shop_name] = new_shop
                            shop_obj = shop_cache[found_shop_name]
                            shop_columns.append({'shop': shop_obj, 'col_idx': col_idx})

                if not shop_columns: raise ValueError(f"店舗情報が見つかりませんでした。(販売行:{sales_header_row+1} の上を確認しました)")

                customer_count_cat, _ = Category.objects.get_or_create(code=9999, level=10, defaults={'name': '客数'})
                category_map = {c.code: c for c in Category.objects.filter(level=180)}

                count = 0
                customer_rows_count = 0
                
                data_start_index = sales_header_row + 1 
                
                logger.debug(f"data_start_index: {data_start_index}, Total rows in df: {len(df)}")
                
                with transaction.atomic():
                    # 新しい累計データで同月内の過去日付分を上書きするため、
                    # アップロード対象の店舗について同年同月で report_date より古い日付のレコードを削除する
                    try:
                        shop_ids = [s['shop'].id for s in shop_columns]
                        if shop_ids:
                            SalesRecord.objects.filter(
                                shop_id__in=shop_ids,
                                date__year=report_date.year,
                                date__month=report_date.month,
                                date__lt=report_date
                            ).delete()
                    except Exception:
                        # 削除失敗しても継続（トランザクションによりロールバックされる可能性あり）
                        logger.exception("Failed to prune old monthly records before import")

                    for index, row in df.iloc[data_start_index:].iterrows(): 
                        
                        code_val = row[0]
                        a_col_val = str(code_val).replace("　", "").replace(" ", "").strip()
                        name_val = str(row[1]).strip() if len(row) > 1 else ""
                        
                        category_obj = None
                        is_customer_count = False

                        # 客数判定: A列に「客数」が含まれていれば客数行と見なす
                        if "客数" in a_col_val:
                            category_obj = customer_count_cat
                            is_customer_count = True
                            
                        # 客数行でなければ、部門コードの有無を確認
                        elif not pd.isna(code_val):
                            try:
                                # 部門コードとしてA列の値を使用
                                code_int = int(float(a_col_val.replace(',', '')))
                                category_obj = category_map.get(code_int)
                            except:
                                pass
                        
                        if not category_obj:
                            if "客数" in a_col_val:
                                logger.debug(f"客数検出失敗: Index {index}, A列='{a_col_val}', B列='{name_val}'")
                            continue
                        
                        # --- データ登録処理 ---
                        
                        if is_customer_count:
                            logger.debug(f"客数行検出 SUCCESS at Index: {index}. A列値: {a_col_val}")
                            
                            row_registered_count = 0
                            
                            for shop_info in shop_columns:
                                shop = shop_info['shop']
                                col_idx = shop_info['col_idx']
                                
                                # 各店舗の販売列(col_idx)に格納されている客数を取得 (offset=0)
                                sales_val = get_val(row, col_idx, 0) 
                                
                                logger.debug(f"  > Shop: {shop.name} (Col {col_idx + 1}), Sales Value: {sales_val}")
                                
                                # 客数行では、買取、仕入、ネット、粗利は 0
                                if sales_val > 0: # 値が入っている場合のみ登録
                                    SalesRecord.objects.update_or_create(
                                        shop=shop,
                                        category=category_obj,
                                        date=report_date,
                                        defaults={
                                            'amount_sales': sales_val, 
                                            'amount_profit': 0, 
                                            'amount_purchase': 0,
                                            'amount_supply': 0,
                                            'amount_net': 0,
                                        }
                                    )
                                    customer_rows_count += 1
                                    row_registered_count += 1
                                    count += 1
                            
                            if row_registered_count == 0:
                                logger.debug("  > Warning: 客数行として検出されましたが、すべての店舗の客数値が 0 だったため登録されませんでした。")
                            
                            continue # 客数行の処理が完了したため、次の行へ

                        # 通常の部門データ行の場合
                        for shop_info in shop_columns:
                            shop = shop_info['shop']
                            col_idx = shop_info['col_idx']
                            
                            # 通常の部門データ行の場合、5列のオフセットで値を取得
                            sales_val = get_val(row, col_idx, 0)
                            purchase_val = get_val(row, col_idx, 1)
                            supply_val = get_val(row, col_idx, 2)
                            net_val = get_val(row, col_idx, 3)
                            profit_val = get_val(row, col_idx, 4)

                            SalesRecord.objects.update_or_create(
                                shop=shop,
                                category=category_obj,
                                date=report_date,
                                defaults={
                                    'amount_sales': sales_val,
                                    'amount_profit': profit_val,
                                    'amount_purchase': purchase_val,
                                    'amount_supply': supply_val,
                                    'amount_net': net_val,
                                }
                            )
                        count += 1
                
                # キャッシュをクリアする（重要: ダッシュボードビューが古いデータを読み込まないように）
                cache.clear()

                messages.success(request, f"{report_date} のデータ取り込み完了！(合計{count}行 / うち客数行:{customer_rows_count})")
                return redirect('admin:index')

            except Exception as e:
                logger.exception(f"Error importing Excel file: {e}")
                messages.error(request, f"エラー: {e}")
    else: form = ExcelUploadForm()
    return render(request, 'admin/sales_upload.html', {'form': form, 'title': '売上データ取込'})

# views.py の student_dashboard 関数全体を以下に置き換える

from django.db.models import Max as DjangoMax
from django.db.models.functions import ExtractMonth
# views.py の student_dashboard 関数全体を以下に置き換える (v14)

# ... (他の import やヘルパー関数はそのまま) ...


def student_dashboard(request):
    """生徒用ダッシュボード（部門ランキング）

    シンプルで確実な集計ロジックに差し替え。
    - 10 部門（level=10）を基点に、その配下の 180 部門を集計
    - 年ごとの最新日（必要なら月フィルタ）を一括取得して集計
    - 月プルダウンは 1..12 を表示し、データがない月は disabled
    """

    # 年リスト取得
    all_dates = get_all_dates_cached()
    years = sorted(list(set([d.year for d in all_dates])))
    if not years:
        return render(request, 'dashboard.html', {'error': 'データがまだありません。'})

    # 選択年
    selected_year = request.GET.get('year')
    if selected_year and str(selected_year).isdigit():
        selected_year = int(selected_year)
        if selected_year not in years:
            selected_year = years[-1]
    else:
        # デフォルトは、客数(コード9999)のみの年を避け、実売上データがある最新年を選択する
        chosen = None
        for y in reversed(years):
            has_real = SalesRecord.objects.filter(date__year=y).exclude(category__code=9999).exists()
            if has_real:
                chosen = y
                break
        selected_year = chosen if chosen is not None else years[-1]

    # 月プルダウン（DBから存在する月を取得）
    try:
        months_qs = SalesRecord.objects.filter(date__year=selected_year).values_list('date__month', flat=True).distinct()
        # 重複が大量に出る環境に備え、set() で確実にユニーク化する
        available_months = sorted({int(m) for m in months_qs if m})
    except Exception:
        available_months = []

    # 選択月の決定（無ければ最新利用可能月）
    target_month = None
    if request.GET.get('month') and request.GET.get('month').isdigit():
        m = int(request.GET.get('month'))
        if m in available_months:
            target_month = m
    if target_month not in available_months:
        if available_months:
            target_month = available_months[-1]

    month_choices = [{'value': m, 'name': f"{m}月", 'disabled': False if m in available_months else True} for m in range(1, 13)]

    # キャッシュキー
    cache_key = f"dashboard_v3_record_month_{selected_year}_{target_month if target_month else 'all'}_{request.GET.get('parent_id','root')}"
    cached = cache.get(cache_key)
    if cached:
        return render(request, 'dashboard.html', cached)

    # 親カテゴリ・表示カテゴリの決定
    parent_id = request.GET.get('parent_id')
    parent_category = None
    target_categories = []
    breadcrumbs = []
    if parent_id and parent_id.isdigit():
        try:
            parent_category = Category.objects.get(id=parent_id)
            cur = parent_category
            while cur:
                breadcrumbs.insert(0, cur)
                cur = cur.parent

            if parent_category.level == 10:
                target_categories = Category.objects.filter(parent=parent_category).order_by('code')
            else:
                target_categories = [parent_category]
        except Category.DoesNotExist:
            parent_category = None

    if not target_categories:
        target_categories = Category.objects.filter(level=10).exclude(code=9999).order_by('code')

    current_title = parent_category.name if parent_category else '全社（10部門）'

    # 10部門 -> 180部門マップを作成（キャッシュ化された helper を活用）
    cat_map = {}
    l10_categories = Category.objects.filter(level=10).exclude(code=9999)
    for l10 in l10_categories:
        descendants = get_descendant_category_ids(l10.code)
        for d_id in descendants:
            cat_map[d_id] = l10.id

    # --- 修正: ドリルダウン対象の階層(10/35/90/180)に合わせて 180 部門を収集し、
    #     各 180 部門を表示対象カテゴリ(id)へマップする ---
    # キャッシュ化された 180 グループを使って DB への多重アクセスを回避
    groups = get_category_180_groups_cached()
    by_90 = groups['by_90']; by_35 = groups['by_35']; by_10 = groups['by_10']

    cat_map_target = {}  # 180_id -> target_category_id
    for tc in target_categories:
        try:
            if tc.level == 180:
                cat_map_target[tc.id] = tc.id
            elif tc.level == 90:
                ids = by_90.get(tc.id, [])
                for i in ids: cat_map_target[i] = tc.id
            elif tc.level == 35:
                ids = by_35.get(tc.id, [])
                for i in ids: cat_map_target[i] = tc.id
            elif tc.level == 10:
                ids = by_10.get(tc.id, [])
                for i in ids: cat_map_target[i] = tc.id
            else:
                # fallback
                ids = by_10.get(tc.id, []) or []
                for i in ids: cat_map_target[i] = tc.id
        except Exception:
            continue

    relevant_180_ids = list(cat_map_target.keys())

    # 年ごとの最新日をまとめて取得（必要なら month フィルタ）
    latest_cache_key = f"latest_per_year_month_{target_month if target_month else 'all'}"
    year_to_latest = cache.get(latest_cache_key)
    if year_to_latest is None:
        year_to_latest = {}
        # 月フィルタが指定されている場合、ExtractMonth を避けるため年ごとに日付範囲検索して最新日を取得
        if target_month is not None:
            for y in years:
                try:
                    last_day = calendar.monthrange(y, target_month)[1]
                    start = date(y, target_month, 1)
                    end = date(y, target_month, last_day)
                    latest = SalesRecord.objects.filter(date__range=(start, end)).order_by('-date').values_list('date', flat=True).first()
                    if latest:
                        year_to_latest[y] = latest
                except Exception:
                    continue
        else:
            # 月フィルタがない場合は年ごとの最新日を集約で取得（関数適用なし）
            qs = SalesRecord.objects
            latest_per_year_qs = qs.values('date__year').annotate(latest_date=Max('date'))
            year_to_latest = {item['date__year']: item['latest_date'] for item in latest_per_year_qs if item.get('latest_date')}

        # フォールバック: 月フィルタで年別最新が一切見つからなかった場合、
        # 月フィルタを外して年ごとの最新日を取得して表示可能にする
        if not year_to_latest:
            try:
                qs = SalesRecord.objects
                latest_per_year_qs = qs.values('date__year').annotate(latest_date=Max('date'))
                year_to_latest = {item['date__year']: item['latest_date'] for item in latest_per_year_qs if item.get('latest_date')}
            except Exception:
                year_to_latest = {}

        cache.set(latest_cache_key, year_to_latest, 60 * 60 * 24)

    latest_dates = list(year_to_latest.values())

    # 最新日データを一括取得して 180 部門ごとに金額を集計
    records_qs = []
    if latest_dates and relevant_180_ids:
        # 店舗ごとの明細が大量にあるため、DB側で日付＋カテゴリで集約して返す
        records_qs = SalesRecord.objects.filter(
            date__in=latest_dates,
            category_id__in=relevant_180_ids
        ).values('date', 'category_id').annotate(total_sales=Sum('amount_sales'))

    # --- DEBUG: 出力して原因を特定 ---
    try:
        logger.debug("student_dashboard: selected_year=%s", selected_year)
        logger.debug("student_dashboard: years=%s", years)
        logger.debug("student_dashboard: available_months=%s", available_months)
        logger.debug("student_dashboard: target_month=%s", target_month)
        logger.debug("student_dashboard: parent_id=%s", parent_id)
        logger.debug("student_dashboard: target_categories count=%s", len(target_categories))
        logger.debug("student_dashboard: relevant_180_ids count=%s", len(relevant_180_ids))
        logger.debug("student_dashboard: year_to_latest=%s", year_to_latest)
        logger.debug("student_dashboard: latest_dates=%s", latest_dates)
        try:
            # Query count may be expensive; show sample sizes
            if hasattr(records_qs, 'count'):
                logger.debug("student_dashboard: records_qs count (est): %s", records_qs.count())
            else:
                logger.debug("student_dashboard: records_qs count (est): %s", len(list(records_qs)))
        except Exception:
            pass
    except Exception:
        pass

    per_year_dept = {y: {} for y in year_to_latest.keys()}
    for r in records_qs:
        d = r['date']
        y = d.year
        cat180 = r['category_id']
        # DB側で集約した合計を使用
        amount = r.get('total_sales') or 0
        # ドリルダウン対象のカテゴリIDへマップ（10/35/90/180に対応）
        root_id = cat_map_target.get(cat180)
        if not root_id: continue
        if y not in per_year_dept: per_year_dept[y] = {}
        per_year_dept[y][root_id] = per_year_dept[y].get(root_id, 0) + amount

    # DEBUG: per_year_dept summary (年ごとの上位3部門の id:amount を出力)
    try:
        for y, totals in sorted(per_year_dept.items()):
            items = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:3]
            logger.debug("student_dashboard: per_year_dept top3 for %s -> %s", y, items)
        logger.debug("student_dashboard: per_year_dept keys-> %s", sorted(list(per_year_dept.keys())))
    except Exception:
        pass

    # year_data の構築（ランクとシェア）
    year_data = {}
    for y, totals in per_year_dept.items():
        total_sales = sum(totals.values())
        sorted_depts = sorted(totals.items(), key=lambda x: x[1], reverse=True)
        info = {}
        for rank, (cid, amt) in enumerate(sorted_depts, 1):
            share = round((amt / total_sales * 100), 1) if total_sales > 0 else 0
            info[cid] = {'rank': rank, 'share': share, 'amount_total': amt}
        year_data[y] = info

    # DEBUG: year_data sample
    try:
        for y in sorted(year_data.keys()):
            top = sorted(year_data[y].items(), key=lambda x: x[1]['amount_total'], reverse=True)[:3]
            logger.debug("student_dashboard: year_data top3 for %s -> %s", y, top)
    except Exception:
        pass

    # table_data 構築
    table_data = []
    for dept in target_categories:
        row = {'id': dept.id, 'name': dept.name, 'level': dept.level, 'is_clickable': dept.level < 35, 'cells': []}
        for year in years:
            data = year_data.get(year, {}).get(dept.id)
            amount_total = data['amount_total'] if data else None
            # 千円単位で丸めてカンマ区切りを付与した表示文字列を作成
            if amount_total is not None:
                try:
                    # 単位なしでカンマ区切り（実数合計の表示）
                    formatted_amount = f"{int(amount_total):,}"
                except Exception:
                    formatted_amount = None
            else:
                formatted_amount = None

            row['cells'].append({
                'year': year,
                'rank': data['rank'] if data else None,
                'share': data['share'] if data else None,
                'amount_total': amount_total,
                'formatted_amount': formatted_amount,
            })
        table_data.append(row)

    # DEBUG: table_data summary (行数と、各行の非空セルサンプル)
    try:
        logger.debug("student_dashboard: table_data rows=%s", len(table_data))
        for r in table_data[:5]:
            non_empty = [(c['year'], c['rank'], c['amount_total']) for c in r['cells'] if c['rank']]
            logger.debug("student_dashboard: table_row id=%s name=%s non_empty_cells=%s", r['id'], r['name'], non_empty)
    except Exception:
        pass

    latest_year = years[-1] if years else None
    if latest_year and latest_year in year_data:
        table_data.sort(key=lambda x: year_data.get(latest_year, {}).get(x['id'], {}).get('rank', 999))

    context = {
        'years': years,
        'table_data': table_data,
        'breadcrumbs': breadcrumbs,
        'current_title': current_title,
        'month_choices': month_choices,
        'target_month': target_month,
        'selected_year': selected_year,
    }

    cache.set(cache_key, context, 60 * 60 * 24 * 7)
    return render(request, 'dashboard.html', context)

@cache_page(60 * 60 * 24)
def trend_dashboard(request):
    # 全日付キャッシュを取得
    all_dates = get_all_dates_cached()
    if not all_dates:
        return render(request, 'trend_dashboard.html', {'error': 'データがありません。'})

    # 年リストと月の選択状態を構築
    years = sorted(list(set([d.year for d in all_dates])))
    available_months = sorted({d.month for d in all_dates})
    month_choices = [{'value': m, 'name': f"{m}月", 'disabled': False if m in available_months else True} for m in range(1, 13)]

    target_month = None
    if request.GET.get('month') and request.GET.get('month').isdigit():
        m = int(request.GET.get('month'))
        if m in available_months:
            target_month = m

    # 日付リストを決定（target_month があれば各年のその月の最新日を取得）
    dates = []
    # debug_info removed from production; keep local list for potential logging
    debug_info = []
    if target_month is not None:
        for y in years:
            try:
                last_day = calendar.monthrange(y, target_month)[1]
                start = date(y, target_month, 1)
                end = date(y, target_month, last_day)
                latest = SalesRecord.objects.filter(date__range=(start, end)).order_by('-date').values_list('date', flat=True).first()
                if latest:
                    dates.append(latest)
                    debug_info.append(f"year {y}: found {latest}")
                else:
                    debug_info.append(f"year {y}: no data in {start}..{end}")
            except Exception as e:
                debug_info.append(f"year {y}: error {e}")
                continue
        dates = sorted(dates)
    else:
        dates = all_dates

    # 年集計モード: 月未選択時は 2019-2025 の年次合計を表示する
    yearly_mode = False
    years_to_use = None
    if target_month is None:
        yearly_mode = True
        years_to_use = [y for y in range(2019, 2026) if y in years]
        labels = [str(y) for y in years_to_use]
    else:
        labels = [d.strftime('%Y/%m/%d') for d in dates]

    # カテゴリマップ（180 -> 10 部門名）
    cat_map = {}
    # 客数(9999)を除外
    l10s = Category.objects.filter(level=10).exclude(code=9999).order_by('code')
    for l10 in l10s:
        # より堅牢に下位(180)カテゴリを取得する: 再帰的に子を取得するヘルパーを利用
        descendants = get_descendant_ids_for_category(l10.code, 10)
        for d_id in descendants:
            cat_map[d_id] = l10.name

    categories_10 = l10s.order_by('code')
    length = len(years_to_use) if yearly_mode else len(dates)
    dataset_map = {cat.name: [0] * length for cat in categories_10}
    # 金額のマップ（表示用テーブルの元データ）
    amounts_map = {cat.name: [0] * length for cat in categories_10}

    if yearly_mode:
        for i, y in enumerate(years_to_use):
            records = SalesRecord.objects.filter(date__year=y).values('category_id').annotate(total=Sum('amount_sales'))
            yearly_totals = {cat.name: 0 for cat in categories_10}
            for r in records:
                cat_id = r.get('category_id'); amount = r.get('total', 0); root_name = cat_map.get(cat_id)
                if root_name and root_name in yearly_totals:
                    yearly_totals[root_name] += amount
            total_sales = sum(yearly_totals.values())
            # 常に金額配列を格納（表示テーブル用）
            for name in yearly_totals:
                amounts_map[name][i] = yearly_totals.get(name, 0)
            if total_sales > 0:
                for name, amount in yearly_totals.items():
                    dataset_map[name][i] = round((amount / total_sales) * 100, 1)
    else:
        for i, d in enumerate(dates):
            records = SalesRecord.objects.filter(date=d).values('category_id', 'amount_sales')
            daily_totals = {cat.name: 0 for cat in categories_10}
            for r in records:
                cat_id = r['category_id']; amount = r['amount_sales']; root_name = cat_map.get(cat_id)
                if root_name and root_name in daily_totals:
                    daily_totals[root_name] += amount
            total_sales = sum(daily_totals.values())
            # 常に金額配列を格納（表示テーブル用）
            for name in daily_totals:
                amounts_map[name][i] = daily_totals.get(name, 0)
            if total_sales > 0:
                for name, amount in daily_totals.items():
                    dataset_map[name][i] = round((amount / total_sales) * 100, 1)

    colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#E7E9ED', '#71B37C', '#8B4513', '#5D6D7E']
    datasets = []
    for i, (name, data) in enumerate(dataset_map.items()):
        datasets.append({'label': name, 'data': data, 'borderColor': colors[i % len(colors)], 'backgroundColor': colors[i % len(colors)], 'fill': False, 'tension': 0.1})
    # 表示用金額テーブル（文字列でカンマ区切り）を作成
    categories_names = [cat.name for cat in categories_10]
    amounts_table = []
    for name in categories_names:
        row = {'name': name, 'amounts': [f"{int(v):,}" if v else '-' for v in amounts_map.get(name, [])]}
        amounts_table.append(row)

    context = {
        'labels': json.dumps(labels),
        'datasets': json.dumps(datasets),
        'labels_list': labels,
        'datasets_list': [d['data'] for d in datasets],
        # debug_info removed from template output
        'month_choices': month_choices,
        'target_month': target_month,
        'amounts_table': amounts_table,
        'categories_names': categories_names,
    }
    return render(request, 'trend_dashboard.html', context)

@cache_page(60 * 60 * 24)
def shop_ranking(request):
    all_dates = get_all_dates_cached()
    years = sorted(list(set([d.year for d in all_dates])))
    if not years: return render(request, 'shop_ranking.html', {'error': 'データがまだありません。'})
    # 選択年（未指定なら最新年） — 並び替えはこの年のランクを基準に行う
    selected_year = request.GET.get('year')
    if selected_year and str(selected_year).isdigit():
        selected_year = int(selected_year)
        if selected_year not in years:
            selected_year = years[-1]
    else:
        selected_year = years[-1]
    
    checkbox_shops = Shop.objects.exclude(name__in=['加治', 'IMPORT_TEST_STORE']).order_by('name')
    selected_shop_ids = request.GET.getlist('selected_shops')
    if selected_shop_ids: selected_shop_ids = [int(x) for x in selected_shop_ids if x.isdigit()]
    
    # month: 'total' or '1'..'12'
    target_month = request.GET.get('month', 'total')
    month_choices = [('total', '合計')]
    for m in range(1, 13):
        month_choices.append((str(m), f"{m}月"))

    # 客数(9999)を除外
    all_10_depts = Category.objects.filter(level=10).exclude(code=9999).order_by('code')
    selected_dept_code = request.GET.get('dept_code')
    selected_dept_name = "全店合計"
    
    target_category_ids = []
    if selected_dept_code:
        target_category_ids = get_descendant_category_ids(selected_dept_code)
        try: selected_dept_name = Category.objects.get(code=selected_dept_code, level=10).name
        except: pass
    
    year_data = {}
    for year in years:
        if target_month != 'total' and target_month.isdigit():
            m = int(target_month)
            latest_date = SalesRecord.objects.filter(date__year=year, date__month=m).order_by('-date').values_list('date', flat=True).first()
        else:
            latest_date = SalesRecord.objects.filter(date__year=year).order_by('-date').values_list('date', flat=True).first()
        if not latest_date: continue
        
        qs = SalesRecord.objects.filter(date=latest_date)
        if target_category_ids: qs = qs.filter(category__id__in=target_category_ids)
        else: qs = qs.exclude(category__code=9999) # 客数除外
        
        records = qs.values('shop__name', 'amount_sales')
        shop_totals = {}
        for d in records:
            shop_name = d['shop__name']; amount = d['amount_sales']
            # 正規化: 例として和歌山->和歌 などを統合
            shop_name = normalize_shop_name(shop_name)
            if shop_name == "加治": shop_name = "加治木"
            shop_totals[shop_name] = shop_totals.get(shop_name, 0) + amount
        
        sorted_shops = sorted(shop_totals.items(), key=lambda x: x[1], reverse=True)
        year_info = {}
        for rank, (name, amount) in enumerate(sorted_shops, 1): year_info[name] = {'rank': rank}
        year_data[year] = year_info

    all_shops = Shop.objects.all().order_by('name')
    # display_map: display_name -> [shop_id,...]
    display_map = OrderedDict()
    for shop in all_shops:
        if shop.name == "加治": continue
        disp = normalize_shop_name(shop.name)
        display_map.setdefault(disp, []).append(shop.id)

    table_data = []
    for disp, ids in display_map.items():
        # 選択がある場合はグループ内のいずれかが選ばれていれば表示
        if selected_shop_ids and not any(sid in selected_shop_ids for sid in ids):
            continue
        row = {'name': disp, 'cells': []}
        has_data = False
        for i, year in enumerate(years):
            data = year_data.get(year, {}).get(disp, None)
            if data: has_data = True
            rank = data['rank'] if data else None
            diff_icon = ""; diff_class = ""; status_text = ""

            if i > 0:
                prev_year = years[i-1]; prev_data = year_data.get(prev_year, {}).get(disp, None); prev_rank = prev_data['rank'] if prev_data else None
                if rank and not prev_rank: status_text = "新店"; diff_class = "store-new"
                elif not rank and prev_rank:
                    if i == len(years) - 1:
                        status_text = ""
                        diff_class = ""
                    else:
                        status_text = "閉店"
                        diff_class = "store-closed"
                elif rank and prev_rank:
                    if rank < prev_rank: diff = prev_rank - rank; diff_icon = f"↑{diff}"; diff_class = "rank-up"
                    elif rank > prev_rank: diff = rank - prev_rank; diff_icon = f"↓{diff}"; diff_class = "rank-down"
                    else: diff_icon = "→"; diff_class = "rank-same"
            row['cells'].append({'year': year, 'rank': rank, 'diff_icon': diff_icon, 'diff_class': diff_class, 'status_text': status_text})

        if has_data: table_data.append(row)
    
    # 並び替え: 表示グループごとに、選択年で順位がなければ
    # それより新しい年・古い年の順で最も新しい順位を使ってソートする
    # 表示グループ数
    total_groups = len(display_map)
    # 年ごとの有効な順位数を数える
    counts = {y: len(year_data.get(y, {})) for y in years}
    # 選択年が極端にデータ少ない場合は、データが最も多い年をソート基準にする
    max_year = max(counts.items(), key=lambda kv: kv[1])[0] if counts else selected_year
    sort_year = selected_year
    if counts.get(selected_year, 0) < max(1, int(total_groups * 0.5)):
        sort_year = max_year

    def effective_rank_for_display(name):
        # 指定したソート基準年で順位があればそれを返し、なければ新しい年から順に探す
        r = year_data.get(sort_year, {}).get(name, {}).get('rank')
        if r:
            return r
        for y in sorted(years, reverse=True):
            r2 = year_data.get(y, {}).get(name, {}).get('rank')
            if r2:
                return r2
        return 999

    def is_closed_now(row):
        # 最新年にデータが無く、過去にデータがあれば現時点で閉店扱いとする
        if not row.get('cells'):
            return False
        last_cell = row['cells'][-1]
        last_rank = last_cell.get('rank')
        earlier_has = any(c.get('rank') for c in row['cells'][:-1])
        return (last_rank is None) and bool(earlier_has)

    table_data.sort(key=lambda x: (is_closed_now(x), effective_rank_for_display(x['name'])))
    
    context = {
        'years': years, 'table_data': table_data, 
        'checkbox_shops': checkbox_shops, 'selected_shop_ids': selected_shop_ids,
        'all_10_depts': all_10_depts, 'selected_dept_code': selected_dept_code, 'selected_dept_name': selected_dept_name
    }
    context['month_choices'] = month_choices
    context['target_month'] = target_month
    return render(request, 'shop_ranking.html', context)

@cache_page(60 * 60 * 24)
def profit_ranking(request):
    all_dates = get_all_dates_cached()
    years = sorted(list(set([d.year for d in all_dates])))
    if not years: return render(request, 'profit_ranking.html', {'error': 'データがまだありません。'})

    # month: 'total' or '1'..'12'
    target_month = request.GET.get('month', 'total')
    month_choices = [('total', '合計')]
    for m in range(1, 13):
        month_choices.append((str(m), f"{m}月"))
    
    cat_map = {}
    # 客数(9999)を除外
    l10s = Category.objects.filter(level=10).exclude(code=9999)
    for l10 in l10s:
        # 再帰的に下位カテゴリ(180など)の id を取得する共通ヘルパーを利用
        descendants = get_descendant_ids_for_category(l10.code, 10)
        for d_id in descendants: cat_map[d_id] = l10.name
    
    year_data = {}
    for year in years:
        if target_month != 'total' and target_month.isdigit():
            m = int(target_month)
            latest_date = SalesRecord.objects.filter(date__year=year, date__month=m).order_by('-date').values_list('date', flat=True).first()
        else:
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
            profit_rank = i + 1
            sales_rank = sales_ranks.get(name, 0)
            gap = sales_rank - profit_rank
            profit_amount = dept_data.get(name, {}).get('profit', 0)
            year_info[name] = {
                'profit_rank': profit_rank,
                'sales_rank': sales_rank,
                'profit_amount': profit_amount,
                'gap': gap, 'gap_abs': abs(gap), 'margin': round(margin, 1)
            }
        year_data[year] = year_info

    all_depts = Category.objects.filter(level=10).exclude(code=9999).order_by('code')
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
                profit_val = None
                try:
                    profit_val = int(data.get('profit_amount')) if data and data.get('profit_amount') is not None else None
                except Exception:
                    profit_val = None
                if gap > 0:
                    gap_class = "gap-up"
                    gap_icon = (f"{profit_val:,}" if profit_val is not None else "-")
                elif gap < 0:
                    gap_class = "gap-down"
                    gap_icon = (f"{profit_val:,}" if profit_val is not None else "-")
                else:
                    gap_class = "gap-same"
                    gap_icon = (f"{profit_val:,}" if profit_val is not None else "-")
            
            row['cells'].append({'year': year, 'rank': profit_rank, 'gap_class': gap_class, 'gap_icon': gap_icon, 'gap_abs': gap_abs, 'margin': margin})
        table_data.append(row)

    latest_year = years[-1]
    table_data.sort(key=lambda x: year_data.get(latest_year, {}).get(x['name'], {}).get('profit_rank', 999))

    context = {'years': years, 'table_data': table_data, 'month_choices': month_choices, 'target_month': target_month}
    return render(request, 'profit_ranking.html', context)



@cache_page(60 * 60 * 24)
def hyuga_trend(request):
    # 年次集計モード: 各年ごとに 10 部門の年間合計を計算してスタック棒グラフにする
    dates = get_all_dates_cached()
    if not dates:
        return render(request, 'hyuga_trend.html', {'error': 'データがまだありません。'})

    hyuga_shop = Shop.objects.filter(name__contains="日向").first()
    if not hyuga_shop:
        return render(request, 'hyuga_trend.html', {'error': '「日向」という名前の店舗が見つかりませんでした。'})

    years = sorted(list(set([d.year for d in dates])))
    labels = [str(y) for y in years]

    # 客数(9999)を除外
    l10s = Category.objects.filter(level=10).exclude(code=9999).order_by('code')

    cat_map = {}
    for l10 in l10s:
        descendants = get_descendant_ids_for_category(l10.code, 10)
        for d_id in descendants:
            cat_map[d_id] = l10.name

    # datasets: 各部門ごとに years 長の配列を用意
    dataset_map = {cat.name: [0] * len(years) for cat in l10s}

    # 年ごとに集計（DBでカテゴリごとに合計を取得）
    for i, y in enumerate(years):
        records = SalesRecord.objects.filter(date__year=y, shop=hyuga_shop).values('category_id').annotate(total=Sum('amount_sales'))
        yearly_totals = {cat.name: 0 for cat in l10s}
        for r in records:
            cat_id = r.get('category_id')
            amount = r.get('total', 0) or 0
            root_name = cat_map.get(cat_id)
            if root_name:
                yearly_totals[root_name] += amount
        for name, amount in yearly_totals.items():
            dataset_map[name][i] = amount
    
    colors = [
        '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', 
        '#FF9F40', '#E7E9ED', '#71B37C', '#8B4513', '#5D6D7E'
    ]
    
    datasets = []
    for i, (name, data) in enumerate(dataset_map.items()):
        datasets.append({
            'label': name,
            'data': data,
            'backgroundColor': colors[i % len(colors)],
        })
    # --- テーブル用データ作成: 部門を行、年を列にした横断テーブル ---
    def fmt(v):
        try:
            return f"{int(v):,}"
        except Exception:
            return "0"

    table_years = labels
    table_rows = []
    for name, data in dataset_map.items():
        row_vals = [fmt(v) for v in data]
        table_rows.append({'dept': name, 'values': row_vals})

    # 列合計（年ごとの合計）
    totals = []
    for col_idx in range(len(labels)):
        s = 0
        for _, data in dataset_map.items():
            try:
                s += int(data[col_idx])
            except Exception:
                pass
        totals.append(fmt(s))

    context = {
        'shop_name': hyuga_shop.name,
        'labels': json.dumps(labels),
        'datasets': json.dumps(datasets),
        'table_years': table_years,
        'table_rows': table_rows,
        'table_totals': totals,
    }
    return render(request, 'hyuga_trend.html', context)

@cache_page(60 * 60 * 24)
def store_comparison(request):
    all_dates = get_all_dates_cached()
    years = sorted(list(set([d.year for d in all_dates])), reverse=True)
    if not years: return render(request, 'store_comparison.html', {'error': 'データがまだありません。'})
    
    selected_year = request.GET.get('year')
    if selected_year:
        selected_year = int(selected_year)
    else:
        # デフォルトは、客数(コード9999)のみしか無い年を避け、実データがある最新年を選ぶ
        chosen = None
        for y in years:
            has_real = SalesRecord.objects.filter(date__year=y).exclude(category__code=9999).exists()
            if has_real:
                chosen = y
                break
        selected_year = chosen if chosen is not None else years[0]
    
    target_shop_obj = Shop.objects.filter(name__contains="日向").first()
    target_shop_id = target_shop_obj.id if target_shop_obj else None
    
    all_shops = Shop.objects.exclude(name__in=['加治', 'IMPORT_TEST_STORE'])
    if target_shop_id:
        all_shops = all_shops.exclude(id=target_shop_id)
    all_shops = all_shops.order_by('name')

    raw_comparison_ids = request.GET.getlist('comparison_shops')
    include_all_others = 'all_others' in raw_comparison_ids
    include_all_others = 'all_others' in raw_comparison_ids
    
    cat_map = {}
    # 客数(9999)を除外
    l10s = Category.objects.filter(level=10).exclude(code=9999).order_by('code')
    dept_names = [c.name for c in l10s]
    for l10 in l10s:
        descendants = get_descendant_ids_for_category(l10.code, 10)
        for d_id in descendants: cat_map[d_id] = l10.name
        
    chart_data = {'labels': dept_names, 'datasets': []}

    # build display groups and compute selected tokens
    display_map, all_shops_display, _, comparison_shop_ids = build_display_groups(all_shops, raw_comparison_ids)
    selected_display_values = []
    raw_tokens = raw_comparison_ids or []
    for disp, ids in display_map.items():
        token = '|'.join(map(str, ids))
        if any(i in comparison_shop_ids for i in ids) or token in raw_tokens:
            selected_display_values.append(token)
    
    # 年次合計モード: selected_year の年間合計を使って店舗ごとの部門構成比を計算
    qs = SalesRecord.objects.filter(date__year=selected_year)

    if include_all_others:
        # 全店舗（対象店を除く）を "他店合計" として集計
        records = qs.values('shop_id', 'shop__name', 'category_id').annotate(total_sales=Sum('amount_sales'))
    else:
        ids_to_fetch = comparison_shop_ids.copy()
        if target_shop_id:
            ids_to_fetch.append(target_shop_id)
        if ids_to_fetch:
            records = qs.filter(shop_id__in=ids_to_fetch).values('shop_id', 'shop__name', 'category_id').annotate(total_sales=Sum('amount_sales'))
        else:
            records = []

    shop_aggs = {}
    all_others_agg = {'name': '他店合計', 'total': 0, 'depts': {d: 0 for d in dept_names}}

    for r in records:
        sid = r['shop_id']; sname = r['shop__name']
        if sname == "加治": sname = "加治木"

        cat_id = r['category_id']; root_name = cat_map.get(cat_id)
        amount = r.get('total_sales', 0)
        if not root_name: continue

        if (target_shop_id and sid == target_shop_id) or (sid in comparison_shop_ids):
            if sid not in shop_aggs:
                shop_aggs[sid] = {'name': sname, 'total': 0, 'depts': {d: 0 for d in dept_names}}
            shop_aggs[sid]['depts'][root_name] += amount
            shop_aggs[sid]['total'] += amount

        if include_all_others:
            if target_shop_id and sid == target_shop_id: continue
            all_others_agg['depts'][root_name] += amount
            all_others_agg['total'] += amount

    # ターゲット店を先に追加（存在する場合）
    if target_shop_id and target_shop_id in shop_aggs:
        data = shop_aggs[target_shop_id]
        shares = [round((data['depts'][d] / data['total'] * 100), 1) if data['total'] > 0 else 0 for d in dept_names]
        chart_data['datasets'].append({
            'label': data['name'], 'data': shares,
            'backgroundColor': 'rgba(255, 99, 132, 0.7)', 'borderColor': 'rgba(255, 99, 132, 1)', 'borderWidth': 1
        })

    # 他店合計を追加
    if include_all_others:
        data = all_others_agg
        shares = [round((data['depts'][d] / data['total'] * 100), 1) if data['total'] > 0 else 0 for d in dept_names]
        chart_data['datasets'].append({
            'label': data['name'], 'data': shares,
            'backgroundColor': '#6c757d', 'borderColor': '#6c757d', 'borderWidth': 1,
            'borderDash': [5, 5]
        })

    # グループ単位（表示名単位）で比較列を作る
    colors = ['#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40']
    # table_shops_groups: list of {'ids': [...], 'name': display}
    table_shops_groups = []
    # 先にターゲット店を追加（表示の先頭）
    if target_shop_id:
        ts = Shop.objects.filter(id=target_shop_id).first()
        if ts:
            table_shops_groups.append({'ids': [target_shop_id], 'name': ts.name if ts.name != '加治' else '加治木'})

    # 選択された表示トークンの順でグループを追加
    for token in selected_display_values:
        ids = [int(x) for x in token.split('|') if x.isdigit()]
        # 合算データをまとめる
        name = None
        total = 0
        depts_acc = {d: 0 for d in dept_names}
        for sid in ids:
            if sid in shop_aggs:
                sdata = shop_aggs[sid]
                name = name or sdata.get('name')
                total += sdata.get('total', 0) or 0
                for d in dept_names:
                    depts_acc[d] += sdata['depts'].get(d, 0)
        # 名前は正規化表示を使する（display_map順にselected_display_valuesは作っているためtoken順でdisp取得可能）
        if ids:
            table_shops_groups.append({'ids': ids, 'name': name or token})
            # チャート用データを作成
            shares = [round((depts_acc[d] / total * 100), 1) if total > 0 else 0 for d in dept_names]
            color = colors[len(chart_data['datasets']) % len(colors)]
            chart_data['datasets'].append({'label': name or token, 'data': shares, 'backgroundColor': color, 'borderColor': color, 'borderWidth': 1})

    # --- テーブル用データ作成: 日向店(ターゲット) と 比較店舗/他店合計 ---
    def format_amount(v):
        try:
            return f"{int(v):,}"
        except Exception:
            return "0"

    target_amounts = None
    if target_shop_id and target_shop_id in shop_aggs:
        d = shop_aggs[target_shop_id]
        rows = []
        for dep in dept_names:
            rows.append({'dept': dep, 'amount': format_amount(d['depts'].get(dep, 0))})
        target_amounts = {
            'name': d['name'],
            'total': format_amount(d['total']),
            'rows': rows
        }

    comparison_tables = []
    if include_all_others:
        ao = all_others_agg
        rows = []
        for dep in dept_names:
            rows.append({'dept': dep, 'amount': format_amount(ao['depts'].get(dep, 0))})
        comparison_tables.append({'name': ao['name'], 'total': format_amount(ao['total']), 'rows': rows})

    # グループ単位で table_shops_groups に基づいてテーブルを作る
    for grp in table_shops_groups:
        # target は後で先頭に入れるため比較テーブルにはスキップ
        if target_shop_id and grp.get('ids') == [target_shop_id]:
            continue
        ids = grp.get('ids', [])
        # 合算
        total = 0
        depts_acc = {d: 0 for d in dept_names}
        for sid in ids:
            if sid in shop_aggs:
                sdata = shop_aggs[sid]
                total += sdata.get('total', 0) or 0
                for d in dept_names:
                    depts_acc[d] += sdata['depts'].get(d, 0)
        rows = []
        for dep in dept_names:
            rows.append({'dept': dep, 'amount': format_amount(depts_acc.get(dep, 0))})
        comparison_tables.append({'name': grp.get('name'), 'total': format_amount(total), 'rows': rows})

    # --- テーブル行列データを作成（テンプレートで横断テーブルを表示しやすくする） ---
    table_shops = []
    if target_amounts:
        table_shops.append({'name': target_amounts['name'], 'total': target_amounts['total'], 'rows': target_amounts['rows']})
    for s in comparison_tables:
        table_shops.append({'name': s['name'], 'total': s['total'], 'rows': s['rows']})

    table_rows = []
    for i, dep in enumerate(dept_names):
        values = []
        for shop in table_shops:
            try:
                val = shop['rows'][i]['amount']
            except Exception:
                val = '0'
            values.append(val)
        table_rows.append({'dept': dep, 'values': values})

    context = {
        'years': years, 'all_shops': all_shops_display,
        'selected_year': selected_year,
        'target_shop_name': target_shop_obj.name if target_shop_obj else "日向（未登録）",
        'comparison_shop_ids': comparison_shop_ids,
        'selected_display_values': selected_display_values,
        'include_all_others': include_all_others,
        'chart_data': json.dumps(chart_data),
        'dept_names': dept_names,
        'target_amounts': target_amounts,
        'comparison_tables': comparison_tables,
        'table_shops': table_shops,
        'table_rows': table_rows,
    }
    return render(request, 'store_comparison.html', context)

@cache_page(60 * 60 * 24)
def customer_net_trend(request):
    all_dates = get_all_dates_cached()
    years = sorted(list(set([d.year for d in all_dates])))
    if not years:
        return render(request, 'customer_net_trend.html', {'error': 'データがまだありません。'})
    
    target_shop_obj = Shop.objects.filter(name__contains="日向").first()
    target_shop_id = target_shop_obj.id if target_shop_obj else None
    
    all_shops = Shop.objects.exclude(name__in=['加治', 'IMPORT_TEST_STORE'])
    if target_shop_id:
        all_shops = all_shops.exclude(id=target_shop_id)
    all_shops = all_shops.order_by('name')

    raw_comparison_ids = request.GET.getlist('comparison_shops')
    # comparison_shops may contain combined ids like '12|34' from grouped display checkboxes
    comparison_shop_ids = []
    for token in raw_comparison_ids:
        parts = re.split('[,|]+', token)
        for p in parts:
            if p.isdigit():
                comparison_shop_ids.append(int(p))
    # keep order, unique
    comparison_shop_ids = list(dict.fromkeys(comparison_shop_ids))

    # month: 'total' or '1'..'12'
    target_month = request.GET.get('month', 'total')
    month_choices = [('total', '合計')]
    for m in range(1, 13):
        month_choices.append((str(m), f"{m}月"))
    
    target_ids = []
    if target_shop_id: target_ids.append(target_shop_id)
    target_ids.extend(comparison_shop_ids)
    
    shop_data = {}
    # キャッシュキー (選択店舗, 月, 年の並び)
    years_str = '_'.join(map(str, years))
    ids_key = ','.join(map(str, sorted(target_ids))) if target_ids else 'none'
    cache_key = f'customer_net_trend:{ids_key}:{target_month}:{years_str}'
    cached = cache.get(cache_key)
    if cached is not None:
        shop_data = cached
    else:
        if target_ids:
            shops = Shop.objects.filter(id__in=target_ids)
            for s in shops:
                sname = s.name
                if sname == "加治": sname = "加治木"
                shop_data[s.id] = {
                    'name': sname,
                    'customers': [0] * len(years),
                    'net': [0] * len(years)
                }

    # build display groups once (normalize and grouping)
    raw_comparison_ids = request.GET.getlist('comparison_shops')
    display_map, all_shops_display, selected_display_values, comparison_shop_ids = build_display_groups(all_shops, raw_comparison_ids)

    for i, year in enumerate(years):
        if target_month != 'total' and target_month.isdigit():
            m = int(target_month)
            latest_date = SalesRecord.objects.filter(date__year=year, date__month=m).order_by('-date').values_list('date', flat=True).first()
        else:
            latest_date = SalesRecord.objects.filter(date__year=year).order_by('-date').values_list('date', flat=True).first()

        if not latest_date:
            continue

        # 1. 客数取得 (category__code=9999)
        cust_records = SalesRecord.objects.filter(
            date=latest_date,
            shop_id__in=target_ids,
            category__code=9999
        ).values('shop_id', 'amount_sales')

        for r in cust_records:
            sid = r['shop_id']
            if sid in shop_data:
                shop_data[sid]['customers'][i] = r['amount_sales']

        # 2. ネット売上取得 (全カテゴリのamount_netの合計)
        net_records = SalesRecord.objects.filter(
            date=latest_date,
            shop_id__in=target_ids
        ).values('shop_id').annotate(total_net=Sum('amount_net'))

        for r in net_records:
            sid = r['shop_id']
            if sid in shop_data:
                shop_data[sid]['net'][i] = r['total_net']

        # キャッシュに保存（1時間）
        if cached is None:
            try:
                cache.set(cache_key, shop_data, 60 * 60)
            except Exception:
                logger.exception('cache set failed for %s', cache_key)

        

    # Chart.js データ構築（グループ化して和歌山->和歌 を統合）
    customer_chart = {'labels': [str(y) for y in years], 'datasets': []}
    net_chart = {'labels': [str(y) for y in years], 'datasets': []}

    colors = ['#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#E7E9ED']

    # 日向店 (赤)
    if target_shop_id and target_shop_id in shop_data:
        d = shop_data[target_shop_id]
        common_style = {
            'label': d['name'],
            'borderColor': 'rgba(255, 99, 132, 1)',
            'backgroundColor': 'rgba(255, 99, 132, 0.2)',
            'tension': 0.1, 'fill': False
        }
        customer_chart['datasets'].append({**common_style, 'data': d['customers']})
        net_chart['datasets'].append({**common_style, 'data': d['net']})

    # グループ単位で集計（display_map の順）
    display_group = OrderedDict()
    for disp, ids in display_map.items():
        # only include groups that have any selected ids
        if any(i in comparison_shop_ids for i in ids):
            cust = [0] * len(years)
            net = [0] * len(years)
            name = None
            for sid in ids:
                if sid in shop_data:
                    sdata = shop_data[sid]
                    name = name or sdata.get('name')
                    for xi, v in enumerate(sdata.get('customers', [])):
                        try:
                            cust[xi] += v or 0
                        except Exception:
                            pass
                    for xi, v in enumerate(sdata.get('net', [])):
                        try:
                            net[xi] += v or 0
                        except Exception:
                            pass
            display_group[disp] = {'name': name or disp, 'customers': cust, 'net': net}

    # add grouped datasets
    for idx, (disp, info) in enumerate(display_group.items()):
        color = colors[idx % len(colors)]
        common_style = {'label': info['name'], 'borderColor': color, 'backgroundColor': color, 'tension': 0.1, 'fill': False}
        customer_chart['datasets'].append({**common_style, 'data': info['customers']})
        net_chart['datasets'].append({**common_style, 'data': info['net']})

    # テーブル用データ (表示はカンマ区切り) — 日向→グループ順
    customer_table = []
    net_table = []
    # 日向
    if target_shop_id and target_shop_id in shop_data:
        d = shop_data[target_shop_id]
        customer_table.append({'name': d['name'], 'amounts': [f"{int(x):,}" if x else '-' for x in d['customers']]})
        net_table.append({'name': d['name'], 'amounts': [f"{int(x):,}" if x else '-' for x in d['net']]})

    # グループ
    for disp, info in display_group.items():
        customer_table.append({'name': info['name'], 'amounts': [f"{int(x):,}" if x else '-' for x in info['customers']]})
        net_table.append({'name': info['name'], 'amounts': [f"{int(x):,}" if x else '-' for x in info['net']]})

    context = {
        'years': years,
        'all_shops': all_shops_display,
        'all_shops_count': len(all_shops),
        'all_shops_display_count': len(all_shops_display),
        'target_shop_name': target_shop_obj.name if target_shop_obj else "日向（未登録）",
        'comparison_shop_ids': comparison_shop_ids,
        'selected_display_values': selected_display_values,
        'customer_chart': json.dumps(customer_chart),
        'net_chart': json.dumps(net_chart),
        'month_choices': month_choices,
        'target_month': target_month,
        'customer_table': customer_table,
        'net_table': net_table,
    }
    return render(request, 'customer_net_trend.html', context)

@cache_page(60 * 60 * 24)
def hyuga_vs_others_trend(request):
    all_dates = get_all_dates_cached()
    years = sorted(list(set([d.year for d in all_dates])))
    if not years: return render(request, 'hyuga_vs_others.html', {'error': 'データがまだありません。'})

    # 日向店
    target_shop_obj = Shop.objects.filter(name__contains="日向").first()
    target_shop_id = target_shop_obj.id if target_shop_obj else None
    # ensure this exists on all control paths for templates and static analysis
    selected_display_values = []

    # 比較店舗リスト
    all_shops = Shop.objects.exclude(name__in=['加治', 'IMPORT_TEST_STORE'])
    if target_shop_id: all_shops = all_shops.exclude(id=target_shop_id)
    all_shops = all_shops.order_by('name')

    raw_comparison_ids = request.GET.getlist('comparison_shops')
    display_map, all_shops_display, helper_selected_display_values, comparison_shop_ids = build_display_groups(all_shops, raw_comparison_ids)
    selected_display_values = helper_selected_display_values or []

    # フォームからの入力
    selected_dept_code = request.GET.get('dept_code')
    # month: 'total' or '1'..'12'
    selected_month = request.GET.get('month', 'total')
    
    # 部門リスト（客数除外）
    all_10_depts = Category.objects.filter(level=10).exclude(code=9999).order_by('code')
    
    # デフォルト部門（選択がなければ最初のもの）
    if not selected_dept_code and all_10_depts.exists():
        selected_dept_code = str(all_10_depts[0].code)
    
    selected_dept_name = "部門"
    target_category_ids = []
    if selected_dept_code:
        try:
            cat = Category.objects.get(code=selected_dept_code, level=10)
            selected_dept_name = cat.name
            target_category_ids = get_descendant_category_ids(selected_dept_code)
        except: pass

    # データ集計用
    # { shop_id: [year1_sales, year2_sales, ...], ... }
    shop_data = {}
    target_ids = []
    if target_shop_id: target_ids.append(target_shop_id)
    target_ids.extend(comparison_shop_ids)

    if target_ids:
        shops = Shop.objects.filter(id__in=target_ids)
        for s in shops:
            sname = s.name
            if sname == "加治": sname = "加治木"
            shop_data[s.id] = {'name': sname, 'sales': [0] * len(years)}
    
    for i, year in enumerate(years):
        # 集計モード: トータル（年合計）または特定月の合計
        if selected_month and selected_month != 'total' and str(selected_month).isdigit():
            m = int(selected_month)
            try:
                last_day = calendar.monthrange(year, m)[1]
                start = date(year, m, 1)
                end = date(year, m, last_day)
                records = SalesRecord.objects.filter(
                    date__range=(start, end),
                    shop_id__in=target_ids,
                    category_id__in=target_category_ids
                ).values('shop_id').annotate(total_sales=Sum('amount_sales'))
            except Exception:
                records = []
        else:
            # 年合計
            records = SalesRecord.objects.filter(
                date__year=year,
                shop_id__in=target_ids,
                category_id__in=target_category_ids
            ).values('shop_id').annotate(total_sales=Sum('amount_sales'))
        
        for r in records:
            sid = r['shop_id']
            val = r['total_sales']
            if sid in shop_data:
                shop_data[sid]['sales'][i] = val

    # Chart.js データ
    chart_data = {'labels': [str(y) for y in years], 'datasets': []}
    colors = ['#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#E7E9ED']

    # グループ単位で比較データを作る（表示名を正規化して和歌山→和歌に集約）
    from collections import OrderedDict as _OD
    display_group_sales = _OD()
    # build mapping of display->ids from earlier display_map
    for disp, ids in display_map.items():
        # include this display only if any of its ids are in requested comparison_shop_ids
        if any(i in comparison_shop_ids for i in ids):
            # aggregate sales across member ids
            agg = [0] * len(years)
            name = None
            for sid in ids:
                if sid in shop_data:
                    sname = shop_data[sid].get('name')
                    name = name or sname
                    vals = shop_data[sid].get('sales', [])
                    for xi, v in enumerate(vals):
                        try:
                            agg[xi] += v or 0
                        except Exception:
                            pass
            display_group_sales[disp] = {'ids': ids, 'name': name or disp, 'sales': agg}

    # 日向店（ターゲット）は最初に追加
    if target_shop_id and target_shop_id in shop_data:
        d = shop_data[target_shop_id]
        chart_data['datasets'].append({
            'label': d['name'],
            'data': d['sales'],
            'borderColor': 'rgba(255, 99, 132, 1)',
            'backgroundColor': 'rgba(255, 99, 132, 0.2)',
            'tension': 0.1, 'fill': False
        })

    # グループ化した比較店を追加（display_map の順序に基づく）
    for idx, (disp, info) in enumerate(display_group_sales.items()):
        color = colors[idx % len(colors)]
        chart_data['datasets'].append({
            'label': info['name'],
            'data': info['sales'],
            'borderColor': color, 'backgroundColor': color,
            'tension': 0.1, 'fill': False
        })
    # テーブル用データ (部門が固定, 年が列)
    def fmt_num(v):
        try:
            return f"{int(v):,}"
        except Exception:
            return "0"

    table_shops = []
    # 日向を先頭に追加
    if target_shop_id and target_shop_id in shop_data:
        d = shop_data[target_shop_id]
        table_shops.append({'name': d['name'], 'total': fmt_num(sum(d['sales'])), 'values': [fmt_num(x) for x in d['sales']]})

    # 比較店舗を追加
    # 比較は表示名で集約したグループ単位で追加
    for disp, info in display_group_sales.items():
        vals = info.get('sales', [])
        total = sum([v or 0 for v in vals])
        table_shops.append({'name': info.get('name', disp), 'total': fmt_num(total), 'values': [fmt_num(x) for x in vals]})

    # 行集合 (部門が選択済みのため行は年ごとの値のみ: we show years as columns)
    table_years = [str(y) for y in years]
    table_rows = []
    # For this view rows are not by department (since we show selected department), we show years as columns already provided per shop
    # We'll build rows per year where first cell is year and following are each shop's value
    for i, y in enumerate(years):
        row_vals = [shop['values'][i] for shop in table_shops]
        table_rows.append({'year': str(y), 'values': row_vals})

    context = {
        'years': years,
        'all_shops': all_shops_display,
        'all_10_depts': all_10_depts,
        'selected_dept_code': selected_dept_code,
        'selected_dept_name': selected_dept_name,
        'selected_month': selected_month,
        'selected_display_values': selected_display_values,
        'months': [str(i) for i in range(1, 13)],
        'target_shop_name': target_shop_obj.name if target_shop_obj else "日向（未登録）",
        'comparison_shop_ids': comparison_shop_ids,
        'chart_data': json.dumps(chart_data),
        'table_shops': table_shops,
        'table_rows': table_rows,
        'table_years': table_years,
    }
    return render(request, 'hyuga_vs_others.html', context)


@cache_page(60 * 60 * 24)
def hyuga_vs_others_compare(request):
    """新：部門レベルを選べる日向 vs 他店 比較ページ
    フォーム: dept_level (10/35/90/180), dept_code (code at that level), month, comparison_shops
    """
    all_dates = get_all_dates_cached()
    years = sorted(list(set([d.year for d in all_dates])))
    if not years:
        return render(request, 'hyuga_vs_others_compare.html', {'error': 'データがまだありません。'})

    target_shop_obj = Shop.objects.filter(name__contains="日向").first()
    target_shop_id = target_shop_obj.id if target_shop_obj else None

    all_shops = Shop.objects.exclude(name__in=['加治', 'IMPORT_TEST_STORE'])
    if target_shop_id:
        all_shops = all_shops.exclude(id=target_shop_id)
    all_shops = all_shops.order_by('name')

    # フォーム入力: 部門レベル, 年, 月
    dept_level = int(request.GET.get('dept_level', 10))
    selected_year = request.GET.get('year')
    selected_month = request.GET.get('month')
    raw_comparison_ids = request.GET.getlist('comparison_shops')
    comparison_shop_ids = []
    for token in raw_comparison_ids:
        parts = re.split('[,|]+', token)
        for p in parts:
            if p.isdigit():
                comparison_shop_ids.append(int(p))
    comparison_shop_ids = list(dict.fromkeys(comparison_shop_ids))

    # 部門リスト（選択レベル） — テーブル行はこのレベルの部門一覧になる
    all_depts_at_level = Category.objects.filter(level=dept_level).exclude(code=9999).order_by('code')

    # デフォルト年/月: 最新日を使う
    all_dates = get_all_dates_cached()
    latest = None
    if all_dates:
        latest = max(all_dates)
    if not selected_year or not selected_month:
        if latest:
            selected_year = selected_year or str(latest.year)
            selected_month = selected_month or str(latest.month)
        else:
            selected_year = selected_year or str(datetime.now().year)
            selected_month = selected_month or '1'

    # 集計：テーブルの列は店舗（先頭が日向）、行は選択レベルの部門
    shop_data = {}
    target_ids = []
    if target_shop_id: target_ids.append(target_shop_id)
    target_ids.extend(comparison_shop_ids)

    # 対象年月の範囲
    try:
        y = int(selected_year); m = int(selected_month)
        last_day = calendar.monthrange(y, m)[1]
        start = date(y, m, 1); end = date(y, m, last_day)
    except Exception:
        # fallback: use entire year if month invalid
        try:
            y = int(selected_year)
            start = date(y, 1, 1); end = date(y, 12, 31)
        except Exception:
            start = None; end = None

    # build display groups for this page
    raw_comparison_ids = request.GET.getlist('comparison_shops')
    display_map, all_shops_display, selected_display_values, comparison_shop_ids = build_display_groups(all_shops, raw_comparison_ids)

    # 表示中の列（先頭に日向を入れる） — 各列は複数 shop id を含む可能性あり
    table_shops = []
    if target_shop_id:
        ts = Shop.objects.filter(id=target_shop_id).first()
        if ts:
            table_shops.append({'ids': [target_shop_id], 'name': normalize_shop_name(ts.name)})

    # comparison_shop_ids は個別 id のリスト。ここでは display_map を用いて選択された表示名ごとに ids をまとめる
    selected_display_names = []
    for disp, ids in display_map.items():
        if any(i in comparison_shop_ids for i in ids):
            selected_display_names.append(disp)
            table_shops.append({'ids': ids, 'name': disp})

    # 各部門（選択レベル）ごとに、各店舗表示列（idsの集合）の売上合計を求める
    def fmt_num(v):
        try: return f"{int(v):,}"
        except: return "0"

    # 利用可能な指標と、GET パラメータで選択された指標を扱う
    available_metrics = [('sales', '販売'), ('purchase', '買取'), ('supply', '仕入'), ('net', 'ネット'), ('profit', '粗利')]
    selected_metrics = request.GET.getlist('metrics')
    if not selected_metrics:
        selected_metrics = ['sales']
    metric_label_map = {k: v for k, v in available_metrics}
    metric_keys = [k for k in selected_metrics if k in metric_label_map]
    if not metric_keys:
        metric_keys = ['sales']
    metric_labels = [metric_label_map[k] for k in metric_keys]
    metric_fields = {
        'sales': 'amount_sales',
        'purchase': 'amount_purchase',
        'supply': 'amount_supply',
        'net': 'amount_net',
        'profit': 'amount_profit',
    }

    table_rows = []
    # 各部門ごとに、各店舗の選択指標を取得して値配列に格納する（店舗ごとに指標が横並びになる）
    for dept in all_depts_at_level:
        cat_ids = get_descendant_ids_for_category(dept.code, dept_level)
        row_vals = []
        for shop_col in table_shops:
            shop_ids = shop_col.get('ids', [])
            if start and end:
                qs = SalesRecord.objects.filter(date__range=(start, end), shop_id__in=shop_ids, category_id__in=cat_ids)
            else:
                qs = SalesRecord.objects.filter(shop_id__in=shop_ids, category_id__in=cat_ids)
            # 選択された指標だけを集計する
            agg_kwargs = {mk: Sum(metric_fields.get(mk)) for mk in metric_keys}
            agg = qs.aggregate(**agg_kwargs)
            for k in metric_keys:
                row_vals.append(fmt_num(agg.get(k) or 0))
        table_rows.append({'dept_name': dept.name, 'values': row_vals})

    # テーブル合計（店舗ごと・指標ごと）: 各店舗ごとに metric_keys 数の合計を持つリストを作る
    totals_per_shop = []
    for sidx, shop_col in enumerate(table_shops):
        per_metrics = []
        for mk in range(len(metric_keys)):
            ssum = 0
            for row in table_rows:
                try:
                    val = row['values'][sidx * len(metric_keys) + mk]
                    ssum += int(val.replace(',', ''))
                except Exception:
                    pass
            per_metrics.append(fmt_num(ssum))
        totals_per_shop.append(per_metrics)

    # テーブル行を指標ごとの構造に変換:
    # table_rows_structured: [{dept_name: str, metrics: [[shop1,shop2,...], ... per metric ]}]
    table_rows_structured = []
    num_shops = len(table_shops)
    metrics_count = len(metric_keys)
    for row in table_rows:
        metrics_list = []
        for mk in range(metrics_count):
            vals = []
            for sidx in range(num_shops):
                try:
                    vals.append(row['values'][sidx * metrics_count + mk])
                except Exception:
                    vals.append('0')
            metrics_list.append(vals)
        table_rows_structured.append({'dept_name': row['dept_name'], 'metrics': metrics_list})

    # totals_per_metric: list of per-metric lists of shop totals
    totals_per_metric = []
    for mk in range(metrics_count):
        per_shop_totals = []
        for sidx in range(num_shops):
            try:
                per_shop_totals.append(totals_per_shop[sidx][mk])
            except Exception:
                per_shop_totals.append('0')
        totals_per_metric.append(per_shop_totals)

    # Chart.js データ: x軸 = 部門名, datasets per display-column (選択された最初の指標を表示)
    chart_data = {'labels': [r['dept_name'] for r in table_rows], 'datasets': []}
    colors = ['#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#E7E9ED']
    for idx, shop_col in enumerate(table_shops):
        data_vals = []
        for row in table_rows:
            try:
                data_vals.append(int(row['values'][idx * len(metric_keys) + 0].replace(',', '')))
            except Exception:
                data_vals.append(0)
        color = colors[idx % len(colors)]
        chart_data['datasets'].append({'label': shop_col.get('name'), 'data': data_vals, 'borderColor': color, 'backgroundColor': color, 'tension': 0.1, 'fill': False})

    # (chart_data/table_shops for department comparison already prepared above)

    context = {
        'years': years,
        'all_shops': all_shops_display,
        'comparison_shop_ids': comparison_shop_ids,
        'selected_display_names': selected_display_names,
        'dept_levels': [10,35,90,180],
        'all_depts_at_level': all_depts_at_level,
        'dept_level': dept_level,
        'selected_year': selected_year,
        'selected_month': selected_month,
        'months': [str(i) for i in range(1,13)],
        'available_metrics': available_metrics,
        'selected_metrics': selected_metrics,
        'metric_keys': metric_keys,
        'metric_labels': metric_labels,
        'table_shops': [{'ids': s.get('ids'), 'name': s.get('name')} for s in table_shops],
        'table_rows': table_rows,
        'table_rows_structured': table_rows_structured,
        'table_totals': totals_per_shop,
        'totals_per_metric': totals_per_metric,
        'chart_data': json.dumps(chart_data),
        'js_table_rows': json.dumps(table_rows_structured),
        'js_table_shops': json.dumps([s.get('name') for s in table_shops]),
        'js_metric_keys': json.dumps(metric_keys),
        'js_metric_labels': json.dumps(metric_labels),
    }
    try:
        logger.debug("hyuga_compare: table_shops=%s, table_rows=%s, table_rows_structured=%s, totals_per_metric=%s, chart_labels=%s",
                     len(context.get('table_shops', [])),
                     len(context.get('table_rows', [])),
                     len(context.get('table_rows_structured', [])),
                     len(context.get('totals_per_metric', [])),
                     len(chart_data.get('labels', [])) if isinstance(chart_data, dict) else 0)
    except Exception:
        logger.exception("failed to log hyuga_compare debug info")
    return render(request, 'hyuga_vs_others_compare.html', context)


def hyuga_vs_others_compare_csv(request):
    """CSV ダウンロード：`hyuga_vs_others_compare` と同じ集計を行い、CSV を返す"""
    # パラメータ
    dept_level = int(request.GET.get('dept_level', 10))
    selected_year = request.GET.get('year')
    selected_month = request.GET.get('month')
    raw_comparison_ids = request.GET.getlist('comparison_shops')
    comparison_shop_ids = []
    for token in raw_comparison_ids:
        parts = re.split('[,|]+', token)
        for p in parts:
            if p.isdigit():
                comparison_shop_ids.append(int(p))
    comparison_shop_ids = list(dict.fromkeys(comparison_shop_ids))

    # target shop 日向
    target_shop_obj = Shop.objects.filter(name__contains="日向").first()
    target_shop_id = target_shop_obj.id if target_shop_obj else None

    # テーブル用店舗リスト
    table_shops = []
    if target_shop_id:
        s = Shop.objects.filter(id=target_shop_id).first()
        if s: table_shops.append(s)
    for sid in comparison_shop_ids:
        s = Shop.objects.filter(id=sid).first()
        if s: table_shops.append(s)

    # 指標
    available_metrics = [('sales', '販売'), ('purchase', '買取'), ('supply', '仕入'), ('net', 'ネット'), ('profit', '粗利')]
    selected_metrics = request.GET.getlist('metrics')
    if not selected_metrics:
        selected_metrics = ['sales']
    metric_label_map = {k: v for k, v in available_metrics}
    metric_keys = [k for k in selected_metrics if k in metric_label_map]
    if not metric_keys:
        metric_keys = ['sales']
    metric_labels = [metric_label_map[k] for k in metric_keys]
    metric_fields = {
        'sales': 'amount_sales',
        'purchase': 'amount_purchase',
        'supply': 'amount_supply',
        'net': 'amount_net',
        'profit': 'amount_profit',
    }

    # 日付範囲
    try:
        y = int(selected_year); m = int(selected_month)
        last_day = calendar.monthrange(y, m)[1]
        start = date(y, m, 1); end = date(y, m, last_day)
    except Exception:
        try:
            y = int(selected_year)
            start = date(y, 1, 1); end = date(y, 12, 31)
        except Exception:
            start = None; end = None

    # 部門リスト
    all_depts_at_level = Category.objects.filter(level=dept_level).exclude(code=9999).order_by('code')

    def to_int(v):
        try: return int(v)
        except: return 0

    # テーブル行作成（flat）
    table_rows = []
    for dept in all_depts_at_level:
        cat_ids = get_descendant_ids_for_category(dept.code, dept_level)
        row_vals = []
        for shop in table_shops:
            if start and end:
                qs = SalesRecord.objects.filter(date__range=(start, end), shop_id=shop.id, category_id__in=cat_ids)
            else:
                qs = SalesRecord.objects.filter(shop_id=shop.id, category_id__in=cat_ids)
            agg_kwargs = {mk: Sum(metric_fields.get(mk)) for mk in metric_keys}
            agg = qs.aggregate(**agg_kwargs)
            for k in metric_keys:
                row_vals.append(str(to_int(agg.get(k) or 0)))
        table_rows.append({'dept_name': dept.name, 'values': row_vals})

    # totals
    num_shops = len(table_shops)
    metrics_count = len(metric_keys)
    totals = []
    for sidx in range(num_shops):
        for mk in range(metrics_count):
            ssum = 0
            for row in table_rows:
                try:
                    ssum += int(row['values'][sidx * metrics_count + mk])
                except Exception:
                    pass
            totals.append(str(ssum))

    # CSV 出力
    filename = f"hyuga_compare_{dept_level}_{selected_year}_{selected_month}.csv"
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')
    writer = csv.writer(response)

    header = ['部門名']
    for s in table_shops:
        for ml in metric_labels:
            header.append(f"{s.name} {ml}")
    writer.writerow(header)

    for row in table_rows:
        writer.writerow([row['dept_name']] + row['values'])

    writer.writerow(['合計'] + totals)

    return response