import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import { api } from '../api/client';

export default function GamesHubPage() {
  const queryClient = useQueryClient();
  const { data: profile, isLoading } = useQuery({
    queryKey: ['games-profile'],
    queryFn: api.getGameProfile,
  });
  const { data: history } = useQuery({
    queryKey: ['games-history'],
    queryFn: api.getGamesHistory,
  });

  const purchase = useMutation({
    mutationFn: api.purchaseUpgrade,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['games-profile'] });
      queryClient.invalidateQueries({ queryKey: ['games-history'] });
    },
  });

  const stats = profile?.stats || {};
  const activeUpgrades = Object.entries(profile?.upgrades || {}).filter(([, level]) => Number(level) > 0);
  const contracts = [
    {
      key: 'classic',
      label: 'Classic Pressure',
      blurb: 'Balanced chamber pacing with room to build examples, chain logic, and convert momentum into a collapse.',
      badge: 'Core contract',
      href: '/games/interrogation?mode=classic',
    },
    {
      key: 'onslaught',
      label: 'Onslaught Contract',
      blurb: 'Hotter room, shorter window, bigger payout. Protect your early combo or the suspect regains the line.',
      badge: 'High risk',
      href: '/games/interrogation?mode=onslaught',
    },
    {
      key: 'precision',
      label: 'Precision Dossier',
      blurb: 'Fewer turns and harsher punishment for hedge language. Clean structure and examples matter more than ever.',
      badge: 'Technical run',
      href: '/games/interrogation?mode=precision',
    },
  ];

  return (
    <div className="games-hub page-stack">
      <section className="games-hero interrogation-command-center">
        <div className="games-hero-copy">
          <div className="eyebrow">Operation</div>
          <h1 className="page-title">A single flagship chamber, now split into replayable contracts.</h1>
          <p className="page-subtitle">
            Interrogation: The Bot Paradox is the command deck now. Choose the contract that matches your mood,
            build upgrades that permanently change the room, and keep pushing for cleaner collapses.
          </p>
          <div className="room-actions">
            <Link to="/games/interrogation?mode=classic" className="game-portal-link primary">Enter The Chamber</Link>
          </div>
        </div>
        <div className="games-hero-stack">
          <div className="game-stat-card">
            <span>Credits</span>
            <strong>{profile?.credits ?? '...'}</strong>
          </div>
          <div className="game-stat-card">
            <span>Streak</span>
            <strong>{profile?.streak ?? '...'}</strong>
          </div>
          <div className="game-stat-card bounty">
            <span>{profile?.daily_bounty?.code ?? 'BOUNTY'}</span>
            <strong>{profile?.daily_bounty?.title ?? 'Loading...'}</strong>
            <p>{profile?.daily_bounty?.description}</p>
          </div>
        </div>
      </section>

      <section className="game-card-grid">
        {contracts.map((contract) => (
          <motion.div key={contract.key} whileHover={{ y: -6 }} className={`game-portal interrogation flagship mode-${contract.key}`}>
            <div className="game-portal-chrome">{contract.badge}</div>
            <h2>{contract.label}</h2>
            <p>{contract.blurb}</p>
            <Link to={contract.href} className="game-portal-link">Launch Contract</Link>
          </motion.div>
        ))}
      </section>

      <section className="games-ops-grid">
        <div className="game-stat-card">
          <span>Collapses</span>
          <strong>{stats.interrogation_collapses ?? 0}</strong>
          <p>Total suspect breakdowns secured across all operations.</p>
        </div>
        <div className="game-stat-card">
          <span>Longest Combo</span>
          <strong>x{stats.longest_combo ?? 0}</strong>
          <p>Your cleanest pressure chain so far.</p>
        </div>
        <div className="game-stat-card">
          <span>Perfect Phases</span>
          <strong>{stats.perfect_phases ?? 0}</strong>
          <p>Phase transitions cracked under live pressure.</p>
        </div>
        <div className="game-stat-card">
          <span>Rare Drops</span>
          <strong>{stats.rare_drops ?? 0}</strong>
          <p>High-value chamber rewards extracted from full collapses.</p>
        </div>
      </section>

      <section className="surface">
        <div className="section-head">
          <h2 className="section-title">Current Doctrine</h2>
          <span className="section-tag">{activeUpgrades.length} upgrades active</span>
        </div>
        <div className="active-upgrades-row">
          {activeUpgrades.length ? (
            activeUpgrades.map(([key, level]) => (
              <div key={key} className="upgrade-chip">
                <strong>{String(key).replace(/_/g, ' ')}</strong>
                <span>Lv.{Number(level)}</span>
              </div>
            ))
          ) : (
            <p className="empty-copy">No chamber upgrades are active yet. Buy into the black market to change future runs.</p>
          )}
        </div>
      </section>

      <section className="surface">
        <div className="section-head">
          <h2 className="section-title">Black Market Loadout</h2>
          <span className="section-tag">{profile?.credits ?? 0} credits ready</span>
        </div>
        <div className="shop-grid">
          {profile?.shop?.map((item: any) => {
            const nextCost = item.cost * (item.level + 1);
            const maxed = item.level >= item.max_level;
            return (
              <div key={item.key} className={`shop-card ${maxed ? 'maxed' : ''}`}>
                <div className="shop-card-topline">
                  <span>{item.label}</span>
                  <strong>Lv.{item.level}</strong>
                </div>
                <p>{item.description}</p>
                <small>{item.effect}</small>
                <button
                  type="button"
                  className={maxed ? 'secondary' : ''}
                  disabled={maxed || purchase.isPending}
                  onClick={() => purchase.mutate({ upgrade_key: item.key })}
                >
                  {maxed ? 'Maxed' : `Upgrade ${nextCost}`}
                </button>
              </div>
            );
          })}
          {!profile?.shop?.length && !isLoading ? <p className="empty-copy">No upgrade catalog loaded yet.</p> : null}
        </div>
      </section>

      <section className="surface">
        <div className="section-head">
          <h2 className="section-title">Recent Operations</h2>
          <span className="section-tag">{history?.entries?.length || 0} records</span>
        </div>
        <div className="games-history-list">
          {history?.entries?.map((entry: any) => (
            <div key={`${entry.kind}-${entry.id}`} className="games-history-row">
              <div>
                <div className="history-title">{entry.title}</div>
                <div className="history-meta">{entry.subtitle} · {entry.created_at?.slice(0, 16)}</div>
              </div>
              <div className="games-history-score">
                <strong>{entry.score}</strong>
                <span>{entry.reward} cr</span>
              </div>
            </div>
          ))}
          {!history?.entries?.length && !isLoading ? <p className="empty-copy">No games played yet.</p> : null}
        </div>
      </section>
    </div>
  );
}
