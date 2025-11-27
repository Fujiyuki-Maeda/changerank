from django.contrib import admin
from django.urls import path
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

    # 6. 生徒用: 荒利ランキング（新規追加）
    path('profits/', views.profit_ranking, name='profit_ranking'),

    # 7. 生徒用: 利益率マップ（新規追加）
    path('map/', views.profit_map, name='profit_map'),
]