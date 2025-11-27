from django import forms

class ExcelUploadForm(forms.Form):
    """Excelファイルアップロード用のフォーム"""
    file = forms.FileField(label='Excelファイルを選択')