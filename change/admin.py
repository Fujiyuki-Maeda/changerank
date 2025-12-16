from django.contrib import admin
from .models import Shop, Category, SalesRecord

@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    """店舗管理"""
    list_display = ('id', 'name')
    search_fields = ('name',)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """部門マスタ管理"""
    list_display = ('code', 'name', 'level', 'parent')
    list_filter = ('level',)
    search_fields = ('code', 'name')
    ordering = ('code',)

@admin.register(SalesRecord)
class SalesRecordAdmin(admin.ModelAdmin):
    """売上データ管理"""
    # ★ここに新しい項目を追加します
    list_display = (
        'date', 
        'shop', 
        'category', 
        'amount_sales',   # 売上
        'amount_profit',  # 粗利
        'amount_purchase',# 買取 (追加)
        'amount_supply',  # 仕入 (追加)
        'amount_net',     # ネット (追加)
    )
    
    list_filter = ('date', 'shop', 'category__level')
    search_fields = ('category__name', 'shop__name')
    date_hierarchy = 'date'