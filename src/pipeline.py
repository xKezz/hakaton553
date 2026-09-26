import pandas as pd
import json

from data_process.loader import ColumnFinder
from data_process.predprocessing import Preprocessor
from segmentation.full_categorised import build_bonus_report

class Pipeline:
    def __init__(self, df: pd.DataFrame, default_region: str = "RU"):
        self.df = df.copy()
        self.default_region = default_region
        self.preprocessor = Preprocessor(self.df, default_region)
        self.column_finder = ColumnFinder(self.df)
        self._phone_col = None
        self._date_col = None
        self._amount_col = None

    def _find_columns(self):
        self._phone_col = self.column_finder.find_phone_column()
        self._date_col = self.column_finder.find_date_column()
        self._amount_col = self.column_finder.find_amount_column()
        if not self._phone_col:
            raise ValueError("Колонка с телефонами не найдена.")
        if not self._date_col:
            raise ValueError("Колонка с датами не найдена.")
        if not self._amount_col:
            raise ValueError("Колонка с суммами не найдена.")

    def predprocess(self) -> pd.DataFrame:
        if not self._phone_col:
            self._find_columns()
        self.df['client_id'] = self.preprocessor.process_phone_column(self._phone_col)
        self.df['purchase_date'] = self.preprocessor.process_date_column(self._date_col)
        self.df['amount'] = self.preprocessor.process_amount_column(self._amount_col)
        self.df = self.df[['client_id', 'purchase_date', 'amount']].dropna()
        if self.df.empty:
            raise ValueError("После предобработки не осталось ни одной валидной строки.")
        return self.df

    def run(self, output_path: str = None, **kwargs) -> dict:
        clean_df = self.predprocess()
        report = build_bonus_report(data=clean_df, **kwargs)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        return report