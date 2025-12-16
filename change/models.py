from django.db import models

class Shop(models.Model):
    """
    店舗マスタ
    """
    name = models.CharField("店舗名", max_length=50, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "店舗"
        verbose_name_plural = "店舗"


class Category(models.Model):
    """
    商品部門マスタ (階層構造対応)
    """
    LEVEL_CHOICES = (
        (10, '10部門'),
        (35, '35部門'),
        (90, '90部門'),
        (180, '180部門'),
    )

    code = models.IntegerField("部門コード") 
    name = models.CharField("部門名", max_length=100)
    level = models.IntegerField("階層レベル", choices=LEVEL_CHOICES, default=180) 

    parent = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='children', 
        verbose_name="親部門"
    )

    def __str__(self):
        return f"[{self.level}部門:{self.code}] {self.name}"

    class Meta:
        verbose_name = "商品部門"
        verbose_name_plural = "商品部門"
        ordering = ['code']
        unique_together = (('code', 'level'),) 


class SalesRecord(models.Model):
    """
    売上実績データ
    """
    date = models.DateField("計上年月日") 
    
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, verbose_name="店舗")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, verbose_name="部門")

    amount_sales = models.IntegerField("売上金額", default=0)  # 販売
    amount_profit = models.IntegerField("粗利金額", default=0) # 粗利
    
    # ★新規追加項目
    amount_purchase = models.IntegerField("買取金額", default=0) # 買取
    amount_supply = models.IntegerField("仕入金額", default=0)  # 仕入
    amount_net = models.IntegerField("ネット金額", default=0)  # ネット

    def __str__(self):
        return f"{self.date} - {self.shop.name} - {self.category.name}"

    class Meta:
        verbose_name = "売上データ"
        verbose_name_plural = "売上データ"
        unique_together = ('shop', 'category', 'date') 
        ordering = ['-date', 'category__code']
        
        # 【修正】インデックス定義：ベースフィールドのみを指定
        indexes = [
            # date フィールドのクエリを高速化するインデックス
            models.Index(fields=['date']),
            # 店舗と日付での絞り込みやソートを高速化
            models.Index(fields=['shop', '-date']), 
            # カテゴリと日付のクエリを高速化
            models.Index(fields=['category', '-date']),
        ]