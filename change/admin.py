from django.contrib import admin
from .models import Shop, Category, SalesRecord

@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    """店舗管理"""
    list_display = ('id', 'name') # 一覧に表示する項目
    search_fields = ('name',)     # 検索ボックスで検索できる項目

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """部門マスタ管理"""
    list_display = ('code', 'name', 'level', 'parent')
    list_filter = ('level',)      # 右側のサイドバーで絞り込み（10部門だけ見る等）
    search_fields = ('code', 'name')
    ordering = ('code',)          # コード順に並べる

@admin.register(SalesRecord)
class SalesRecordAdmin(admin.ModelAdmin):
    """売上データ管理"""
    list_display = ('date', 'shop', 'category', 'amount_sales', 'amount_profit')
    list_filter = ('date', 'shop', 'category__level') # 日付や店で絞り込み
    search_fields = ('category__name', 'shop__name')
    date_hierarchy = 'date'       # 画面上部に日付ドリルダウンナビを表示