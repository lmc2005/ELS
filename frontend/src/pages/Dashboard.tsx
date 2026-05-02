import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { useAppStore } from '../stores';
import { useEffect } from 'react';
import { Link } from 'react-router-dom';

export default function Dashboard() {
  const { setBudgetStatus, monthlyUsedRmb, monthlyBudgetRmb, isBudgetWarning, isBudgetExceeded } = useAppStore();

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  useEffect(() => {
    if (settings) {
      setBudgetStatus(settings);
    }
  }, [settings, setBudgetStatus]);

  const budgetPercent = monthlyBudgetRmb > 0 ? (monthlyUsedRmb / monthlyBudgetRmb) * 100 : 0;

  return (
    <div>
      <h1 className="page-title">Dashboard</h1>

      <div className="card" style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>Monthly Budget</div>
            <div style={{ fontSize: 28, fontWeight: 700 }}>
              ¥{monthlyUsedRmb.toFixed(2)} / ¥{monthlyBudgetRmb}
            </div>
          </div>
          {isBudgetExceeded && <span style={{ color: 'var(--danger)', fontWeight: 600 }}>Budget Exceeded</span>}
          {isBudgetWarning && !isBudgetExceeded && <span style={{ color: 'var(--warning)', fontWeight: 600 }}>Approaching Limit</span>}
        </div>
        <div style={{ background: 'var(--border)', borderRadius: 4, height: 8, marginTop: 12 }}>
          <div style={{
            width: `${Math.min(budgetPercent, 100)}%`,
            height: '100%',
            borderRadius: 4,
            background: isBudgetExceeded ? 'var(--danger)' : isBudgetWarning ? 'var(--warning)' : 'var(--accent)',
            transition: 'width 0.3s',
          }} />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
        <Link to="/speaking" className="card" style={{ textDecoration: 'none', color: 'inherit' }}>
          <h3>Speaking Room</h3>
          <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>Practice English with an AI tutor</p>
        </Link>
        <Link to="/news" className="card" style={{ textDecoration: 'none', color: 'inherit' }}>
          <h3>Daily News</h3>
          <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>Read AI tech and current affairs</p>
        </Link>
        <Link to="/vocab" className="card" style={{ textDecoration: 'none', color: 'inherit' }}>
          <h3>Vocab Images</h3>
          <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>Search words with visual results</p>
        </Link>
        <Link to="/diary" className="card" style={{ textDecoration: 'none', color: 'inherit' }}>
          <h3>Diary</h3>
          <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>Write and get AI feedback</p>
        </Link>
        <Link to="/retell" className="card" style={{ textDecoration: 'none', color: 'inherit' }}>
          <h3>Retell Game</h3>
          <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>Listen, retell, and level up</p>
        </Link>
      </div>
    </div>
  );
}
