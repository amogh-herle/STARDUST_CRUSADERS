import pandas as pd

df = pd.read_csv('outputs/reports/isolation_forest_scored_transactions.csv', low_memory=False)

print('=== OVERALL COUNTS ===')
print(f'Total rows       : {len(df)}')
print(f'Unique accounts  : {df["account_id"].nunique()}')
print(f'Raw ML flags     : {int(df["is_flagged"].sum())} ({df["is_flagged"].mean()*100:.2f}%)')
print(f'Suppressed       : {int(df["suppressed"].sum())}')
print(f'Final alerts     : {int(df["final_flag"].sum())} ({df["final_flag"].mean()*100:.2f}%)')

print()
print('=== RISK TIER (final alerts only) ===')
print(df[df['final_flag']==1]['risk_tier'].value_counts().to_string())

print()
print('=== FINAL ALERTS BY SEGMENT ===')
print(df[df['final_flag']==1]['entity_segment'].value_counts().to_string())

print()
print('=== SUPPRESSION REASONS ===')
reasons = (
    df[df['suppressed']==True]['suppression_reason']
    .str.rstrip(';').str.split(';').explode().value_counts()
)
print(reasons.to_string())

print()
print('=== ACCOUNTS WITH FINAL ALERTS ===')
acc = df[df['final_flag']==1].groupby('account_id').agg(
    final_alerts=('final_flag','sum'),
    max_score=('anomaly_score','max'),
    segment=('entity_segment','first'),
).sort_values('max_score', ascending=False)
print(f'Accounts with >=1 final alert: {len(acc)}')
print()
print('Top 15 most suspicious accounts:')
print(acc.head(15).to_string())

print()
print('=== SEGMENT BREAKDOWN (all rows) ===')
seg = df.groupby('entity_segment').agg(
    rows=('account_id','count'),
    unique_accounts=('account_id','nunique'),
    raw_flags=('is_flagged','sum'),
    suppressed=('suppressed','sum'),
    final_flags=('final_flag','sum'),
)
print(seg.to_string())

print()
print('=== SCORE STATS (final alerts) ===')
fa = df[df['final_flag']==1]['anomaly_score']
print(f'Mean  : {fa.mean():.4f}')
print(f'Median: {fa.median():.4f}')
print(f'Min   : {fa.min():.4f}')
print(f'Max   : {fa.max():.4f}')
print()
print('Distribution:')
cuts = [0.5, 0.65, 0.80, 1.01]
labels = ['Medium 0.50-0.65', 'High 0.65-0.80', 'Critical 0.80-1.0']
bins = pd.cut(fa, bins=[0]+cuts, labels=['<0.5']+labels, include_lowest=True)
print(bins.value_counts().sort_index().to_string())
