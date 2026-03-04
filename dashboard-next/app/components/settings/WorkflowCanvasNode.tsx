'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';

type NodeData = {
  title: string;
  role: string;
  workflow_type: string;
  color: string;
  agent: string;
};

/**
 * 단순 역할 노드 카드 렌더러.
 */
export function WorkflowCanvasNode({ data, selected }: NodeProps) {
  const nodeData = data as unknown as NodeData;
  const isAgentTaskNode = nodeData.workflow_type === 'agent_task';

  return (
    <div className={`wfCanvasNode ${selected ? 'selected' : ''}`} style={{ ['--node-color' as string]: nodeData.color }}>
      <Handle type="target" position={Position.Top} className="wfHandle" />

      <div className="wfNodeHead">
        <strong>{nodeData.title}</strong>
      </div>
      <div className="wfNodeBody">
        <span>{nodeData.role}</span>
        <span className="wfNodeMeta wfNodeAgentMeta">
          {!isAgentTaskNode ? (
            <span className="wfAgentSlotInline" title="Agent 슬롯">
              <span className="wfAgentSlotInlineDot" />
              slot
            </span>
          ) : null}
          <span>{nodeData.agent}</span>
        </span>
      </div>

      {!isAgentTaskNode ? (
        <span className="wfAgentSlotMarker" title="Agent 슬롯" aria-label="Agent 슬롯">
          <span className="wfAgentSlotMarkerDot" />
          <small>agent</small>
        </span>
      ) : null}

      <Handle type="source" position={Position.Bottom} className="wfHandle" />
    </div>
  );
}
