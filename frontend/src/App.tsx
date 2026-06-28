import { useCallback, useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import HomePage from './pages/HomePage';
import PlacementPage from './pages/PlacementPage';
import PlacementCheckpointPage from './pages/PlacementCheckpointPage';
import StudyPage from './pages/StudyPage';
import SessionSummaryPage from './pages/SessionSummaryPage';
import MistakesPage from './pages/MistakesPage';
import ImportPage from './pages/ImportPage';

// Theme: detect system preference + manual toggle
type Theme = 'light' | 'dark';

function initialTheme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme(t => t === 'light' ? 'dark' : 'light');
  }, []);

  return (
    <BrowserRouter>
      <a className="skip-link" href="#main-content">跳到主要內容</a>
      <div id="main-content">
        <Routes>
          <Route path="/" element={<HomePage theme={theme} onToggleTheme={toggleTheme} />} />
          <Route path="/placement" element={<PlacementPage />} />
          <Route path="/placement/checkpoint" element={<PlacementCheckpointPage />} />
          <Route path="/study" element={<StudyPage />} />
          <Route path="/study/summary" element={<SessionSummaryPage />} />
          <Route path="/mistakes" element={<MistakesPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
