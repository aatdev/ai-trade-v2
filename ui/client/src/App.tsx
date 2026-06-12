import { Route, Routes } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import TickerDetail from './pages/TickerDetail';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/ticker/:symbol" element={<TickerDetail />} />
      <Route path="/ticker/:symbol/:date" element={<TickerDetail />} />
    </Routes>
  );
}
