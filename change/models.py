from django.db import models

class Shop(models.Model):
    """
    店舗マスタ
    例: 太宰店, 甘木店, 久留米店
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
    例: 10部門(本) -> 35部門(コミック) -> ... -> 180部門(男子コミック)
    """
    # 階層レベルの定義
    LEVEL_CHOICES = (
        (10, '10部門'),
        (35, '35部門'),
        (90, '90部門'),
        (180, '180部門'),
    )

    # 修正点1: codeから unique=True を削除します
    code = models.IntegerField("部門コード") 
    name = models.CharField("部門名", max_length=100)
    level = models.IntegerField("階層レベル", choices=LEVEL_CHOICES, default=180) 

    # 親部門へのリンク (自分自身への外部キー)
    parent = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='children', 
        verbose_name="親部門"
    )

    def __str__(self):
        # レベルも表示するようにしておくと管理しやすいです
        return f"[{self.level}部門:{self.code}] {self.name}"

    class Meta:
        verbose_name = "商品部門"
        verbose_name_plural = "商品部門"
        ordering = ['code']
        
        # 修正点2: 複合キーを設定します
        # 「code」と「level」の組み合わせで一意（ユニーク）にします
        unique_together = (('code', 'level'),) 


class SalesRecord(models.Model):
    """
    売上実績データ
    """
    date = models.DateField("計上年月日") 
    
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, verbose_name="店舗")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, verbose_name="部門")

    amount_sales = models.IntegerField("売上金額", default=0)
    amount_profit = models.IntegerField("荒利金額", default=0)

    def __str__(self):
        return f"{self.date} - {self.shop.name} - {self.category.name}"

    class Meta:
        verbose_name = "売上データ"
        verbose_name_plural = "売上データ"
        unique_together = ('shop', 'category', 'date') 
        ordering = ['-date', 'category__code']