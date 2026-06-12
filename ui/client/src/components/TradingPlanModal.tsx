import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTradingPlan } from '../api';
import { ErrorNote, Loading, Modal } from './ui';

export default function TradingPlanModal({ onClose }: { onClose: () => void }) {
  const { data, isLoading, error } = useTradingPlan();

  return (
    <Modal title="📋 Торговый план" onClose={onClose} fullscreen footer={<button onClick={onClose}>Закрыть</button>}>
      {isLoading ? (
        <Loading />
      ) : error ? (
        <ErrorNote error={error} />
      ) : data ? (
        <div className="md">
          <Markdown remarkPlugins={[remarkGfm]}>{data.content}</Markdown>
        </div>
      ) : null}
    </Modal>
  );
}
