import { Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom';
import SpeakingRoom from './pages/SpeakingRoom';
import NewsPage from './pages/NewsPage';
import VocabImagePage from './pages/VocabImagePage';
import DiaryPage from './pages/DiaryPage';
import RetellGamePage from './pages/RetellGamePage';
import HistoryPage from './pages/HistoryPage';
import SettingsPage from './pages/SettingsPage';
import GamesHubPage from './pages/GamesHubPage';
import InterrogationGamePage from './pages/InterrogationGamePage';

const navItems = [
  { to: '/games', label: 'Game' },
  { to: '/speaking', label: 'Speaking' },
  { to: '/retell', label: 'Retell' },
  { to: '/news', label: 'News' },
  { to: '/vocab', label: 'Vocab' },
  { to: '/diary', label: 'Diary' },
  { to: '/history', label: 'History' },
  { to: '/settings', label: 'Settings' },
];

export default function App() {
  const location = useLocation();
  const isImmersiveGameRoute = location.pathname === '/games/interrogation';
  const isGameRoute = location.pathname.startsWith('/games/');
  const shellClassName = isImmersiveGameRoute ? 'app-shell immersive-game-shell' : isGameRoute ? 'app-shell game-route' : 'app-shell';
  const mainClassName = isImmersiveGameRoute ? 'content immersive-game-content' : isGameRoute ? 'content game-content' : 'content';

  return (
    <div className={shellClassName}>
      {!isImmersiveGameRoute ? (
        <nav className={isGameRoute ? 'sidebar game-sidebar' : 'sidebar'}>
          <div className="brand-lockup">
            <h1 className="logo">ELS</h1>
            <p className="brand-copy">English Learning Studio</p>
          </div>
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
              {item.label}
            </NavLink>
          ))}
        </nav>
      ) : null}
      <main className={mainClassName}>
        <Routes>
          <Route path="/" element={<Navigate to="/games" replace />} />
          <Route path="/games" element={<GamesHubPage />} />
          <Route path="/games/interrogation" element={<InterrogationGamePage />} />
          <Route path="/speaking" element={<SpeakingRoom />} />
          <Route path="/news" element={<NewsPage />} />
          <Route path="/vocab" element={<VocabImagePage />} />
          <Route path="/diary" element={<DiaryPage />} />
          <Route path="/retell" element={<RetellGamePage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
