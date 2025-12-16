from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from change import views

urlpatterns = [
    # 1. 管理画面
    path('admin/', admin.site.urls),
    
    # 2. 先生用: データ取込
    path('upload-master/', views.upload_category_master, name='upload_master'),
    path('upload-sales/', views.upload_sales_data, name='upload_sales'),
    
    # 3. 生徒用: 部門ランキング（トップページ）
    path('', views.student_dashboard, name='dashboard'),
    
    # 4. 生徒用: 推移グラフ
    path('trends/', views.trend_dashboard, name='trends'),
    
    # 5. 生徒用: 店舗ランキング
    path('shops/', views.shop_ranking, name='shop_ranking'),

    # 6. 生徒用: 粗利ランキング
    path('profits/', views.profit_ranking, name='profit_ranking'),

    
    # 8. 生徒用: 店舗比較
    path('comparison/', views.store_comparison, name='store_comparison'),

    # 9. 生徒用: 日向店推移
    path('hyuga/', views.hyuga_trend, name='hyuga_trend'),

    # 10. 生徒用: 客数・ネット売上推移
    path('customer_net/', views.customer_net_trend, name='customer_net_trend'),
    
    # 11. 生徒用: 日向 vs 他店 部門別推移 (★新規追加)
    path('hyuga_vs_others/', views.hyuga_vs_others_trend, name='hyuga_vs_others_trend'),
    path('hyuga_compare/', views.hyuga_vs_others_compare, name='hyuga_vs_others_compare'),
    path('hyuga_compare_csv/', views.hyuga_vs_others_compare_csv, name='hyuga_vs_others_compare_csv'),
]

# Debug toolbar URLs (only in DEBUG)
if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns