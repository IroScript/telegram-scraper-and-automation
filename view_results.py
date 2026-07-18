#!/usr/bin/env python3
"""View the final scraper results - UTF-8 safe"""
import pandas as pd
import os

file_out = 'output/FINAL_gv_buyers_3days_20260718_110804_00051unique_buyers.xlsx'
if not os.path.exists(file_out):
    print('File not found')
    exit()

df = pd.read_excel(file_out)

print(f'Total unique buyers: {len(df)}')
print(f'Columns: {df.columns.tolist()}')

# Classification breakdown
print('\n=== Classification breakdown ===')
print(df['Classification'].value_counts().to_string())

# Buyers only
buyers = df[df['Classification'] == 'buyer']
print(f'\n=== BUYERS ONLY: {len(buyers)} ===')
for i, (_, row) in enumerate(buyers.iterrows(), 1):
    content = str(row['Content'])[:100]
    print(f'{i:3}. ID: {row["Author ID"]} | {content}')

# Save a clean buyer-only file
if len(buyers) > 0:
    buyer_only_file = 'output/BUYERS_ONLY_' + os.path.basename(file_out)
    buyers.to_excel(buyer_only_file, index=False, engine='openpyxl')
    print(f'\nBuyer-only file saved: {buyer_only_file}')
