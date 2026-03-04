'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  addEdge,
  Background,
  BackgroundVariant,
  Connection,
  Controls,
  Edge,
  MarkerType,
  MiniMap,
  Node,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react';
import { type WorkflowDefinition } from '../../types/dashboard';
import { WorkflowCanvasNode } from './WorkflowCanvasNode';

type RoleType = 'planner' | 'designer' | 'coder' | 'tester' | 'reviewer' | 'escalator';

type FlowNodeData = {
  title: string;
  role: RoleType;
  workflow_type: string;
  color: string;
  agent: string;
  params_text: string;
};

type RoleNodeDef = {
  role: RoleType;
  title: string;
  workflow_type: string;
  color: string;
};

type NodePickerCategory = 'AI' | 'Action in app' | 'Data transformation' | 'Flow' | 'Core' | 'Human in the loop';

type NodePickerCategoryDef = {
  key: NodePickerCategory;
  title: string;
  description: string;
  roles: RoleType[];
};

const ROLE_NODES: RoleNodeDef[] = [
  { role: 'planner', title: '플래너', workflow_type: 'planner_task', color: '#10B981' },
  { role: 'designer', title: '디자이너(Codex)', workflow_type: 'designer_task', color: '#111827' },
  { role: 'coder', title: '코더', workflow_type: 'coder_task', color: '#F59E0B' },
  { role: 'tester', title: '테스터', workflow_type: 'tester_task', color: '#EF4444' },
  { role: 'reviewer', title: '리뷰어', workflow_type: 'reviewer_task', color: '#0EA5E9' },
  { role: 'escalator', title: '중재자(에스컬레이션)', workflow_type: 'claude_escalation', color: '#8B5CF6' },
];

const ROLE_NODE_HINTS: Record<RoleType, string> = {
  planner: '작업 계획 수립 및 이슈 분석',
  designer: '가독성/모던 UI, 모바일 반응형, 컬러/패딩/마진/타이포 디자인 시스템 정의',
  coder: '구현 에이전트 실행',
  tester: '검증/테스트 자동화',
  reviewer: '리뷰 및 품질 점검',
  escalator: '에스컬레이션 중재 및 최종 조정',
};

const NODE_PICKER_CATEGORIES: NodePickerCategoryDef[] = [
  {
    key: 'AI',
    title: 'AI',
    description: '에이전트 작업을 추가합니다.',
    roles: ['planner', 'designer', 'coder', 'reviewer', 'escalator'],
  },
  {
    key: 'Action in app',
    title: 'Action in app',
    description: '외부 앱 동작 노드(준비 중)',
    roles: [],
  },
  {
    key: 'Data transformation',
    title: 'Data transformation',
    description: '데이터 가공 노드(준비 중)',
    roles: [],
  },
  {
    key: 'Flow',
    title: 'Flow',
    description: '흐름 제어 노드를 추가합니다.',
    roles: ['tester'],
  },
  {
    key: 'Core',
    title: 'Core',
    description: '코어 기능 노드(준비 중)',
    roles: [],
  },
  {
    key: 'Human in the loop',
    title: 'Human in the loop',
    description: '사용자 승인 노드(준비 중)',
    roles: [],
  },
];

const DEFAULT_PARAMS = '{\n  "notes": ""\n}';

type Props = {
  initialWorkflow?: WorkflowDefinition | null;
};

function edgeStyle() {
  return { stroke: '#22C55E', strokeWidth: 2 };
}

function findRoleDefByWorkflowType(workflowType: string): RoleNodeDef | null {
  const direct = ROLE_NODES.find((item) => item.workflow_type === workflowType);
  if (direct) return direct;

  // 기존 고정 오케스트레이션 타입과의 호환 매핑
  if (workflowType === 'gemini_plan') return ROLE_NODES.find((item) => item.role === 'planner') ?? null;
  if (workflowType === 'designer_task') return ROLE_NODES.find((item) => item.role === 'designer') ?? null;
  if (workflowType === 'codex_implement' || workflowType === 'codex_fix') return ROLE_NODES.find((item) => item.role === 'coder') ?? null;
  if (workflowType === 'test_after_implement' || workflowType === 'test_after_fix') return ROLE_NODES.find((item) => item.role === 'tester') ?? null;
  if (workflowType === 'gemini_review') return ROLE_NODES.find((item) => item.role === 'reviewer') ?? null;
  return null;
}

/**
 * 단순화된 워크플로우 편집기.
 * 역할(플래너/디자이너/코더/테스터/리뷰어/중재자) + Agent 노드 + LLM 모델 매핑을 제공한다.
 */
export function WorkflowNodeBuilderCard({ initialWorkflow }: Props) {
  const [workflowId, setWorkflowId] = useState('simple_role_flow_v1');
  const [workflowName, setWorkflowName] = useState('Simple Role Flow V1');
  const [workflowDescription, setWorkflowDescription] = useState('역할 기반 단순 워크플로우');
  const [setAsDefault, setSetAsDefault] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  const [registeredAgents, setRegisteredAgents] = useState<string[]>([
    'codex',
    'gemini',
    'claude',
    'shell',
  ]);
  const [newAgentName, setNewAgentName] = useState('');
  const [registeredLlmModels, setRegisteredLlmModels] = useState<string[]>([
    'gemini-3-flash-preview',
    'gpt-5',
    'claude-3-7-sonnet',
  ]);
  const [newLlmModelName, setNewLlmModelName] = useState('');

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<FlowNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const [selectedNodeId, setSelectedNodeId] = useState<string>('');
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>('');
  const [linkedAgentId, setLinkedAgentId] = useState('');
  const [linkedLlmModel, setLinkedLlmModel] = useState('');
  const [addCategory, setAddCategory] = useState<'agent' | 'role'>('role');
  const [addRole, setAddRole] = useState<RoleType>('planner');
  const [addAgent, setAddAgent] = useState('codex');
  const [isNodePickerOpen, setIsNodePickerOpen] = useState(false);
  const [pickerSearch, setPickerSearch] = useState('');
  const [pickerCategory, setPickerCategory] = useState<NodePickerCategory>('AI');

  const nodeTypes = useMemo(() => ({ workflowNode: WorkflowCanvasNode }), []);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );
  const selectedNodeIsRoleTask = Boolean(
    selectedNode
      && selectedNode.data.workflow_type !== 'agent_task'
      && isRoleNodeMappable(selectedNode.data.role),
  );

  const agentNodes = useMemo(
    () => nodes.filter((node) => node.data.workflow_type === 'agent_task'),
    [nodes],
  );

  const availablePickerNodes = useMemo(() => {
    const query = pickerSearch.trim().toLowerCase();
    const categoryDef = NODE_PICKER_CATEGORIES.find((item) => item.key === pickerCategory);
    if (!categoryDef || categoryDef.roles.length === 0) return [];

    return ROLE_NODES.filter((item) => categoryDef.roles.includes(item.role)).filter((item) => {
      if (!query) return true;
      const hint = ROLE_NODE_HINTS[item.role].toLowerCase();
      return (
        item.title.toLowerCase().includes(query)
        || item.workflow_type.toLowerCase().includes(query)
        || hint.includes(query)
      );
    });
  }, [pickerCategory, pickerSearch]);

  /**
   * 대시보드에서 등록한 에이전트 목록을 브라우저에 저장한다.
   * 백엔드 설정 API를 연결하기 전까지는 로컬 저장소 기반으로 빠르게 운영한다.
   */
  useEffect(() => {
    const raw = window.localStorage.getItem('agenthub-registered-agents');
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return;
      const normalized = parsed
        .map((item) => String(item).trim().toLowerCase())
        .filter((item) => item.length > 0);
      if (normalized.length > 0) {
        setRegisteredAgents(Array.from(new Set(normalized)));
      }
    } catch {
      // 로컬 저장소 파싱 오류는 무시하고 기본값으로 계속 진행한다.
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem('agenthub-registered-agents', JSON.stringify(registeredAgents));
  }, [registeredAgents]);

  useEffect(() => {
    if (registeredAgents.length === 0) return;
    if (!registeredAgents.includes(addAgent)) {
      setAddAgent(registeredAgents[0]);
    }
  }, [addAgent, registeredAgents]);

  useEffect(() => {
    const raw = window.localStorage.getItem('agenthub-registered-llm-models');
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return;
      const normalized = parsed
        .map((item) => String(item).trim())
        .filter((item) => item.length > 0);
      if (normalized.length > 0) {
        setRegisteredLlmModels(Array.from(new Set(normalized)));
      }
    } catch {
      // no-op
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem('agenthub-registered-llm-models', JSON.stringify(registeredLlmModels));
  }, [registeredLlmModels]);

  useEffect(() => {
    if (!initialWorkflow) return;

    setWorkflowId(initialWorkflow.workflow_id);
    setWorkflowName(initialWorkflow.name);
    setWorkflowDescription(initialWorkflow.description ?? '');
    setSaveMsg(`로드됨: ${initialWorkflow.workflow_id}`);

    const loadedNodes: Node<FlowNodeData>[] = (initialWorkflow.nodes ?? []).map((node, index) => {
      const roleDef = findRoleDefByWorkflowType(node.type);
      const role: RoleType = roleDef?.role ?? 'coder';
      const color = roleDef?.color ?? '#6B7280';

      return {
        id: node.id,
        type: 'workflowNode',
        position: { x: 120 + (index % 4) * 250, y: 80 + Math.floor(index / 4) * 160 },
        data: {
          title: node.title || roleDef?.title || node.type,
          role,
          workflow_type: node.type,
          color,
          agent: registeredAgents[0] ?? 'codex',
          params_text: DEFAULT_PARAMS,
        },
      };
    });

    const loadedEdges: Edge[] = (initialWorkflow.edges ?? []).map((edge, index) => ({
      id: `e-${edge.from}-${edge.to}-${index}`,
      source: edge.from,
      target: edge.to,
      type: 'smoothstep',
      markerEnd: { type: MarkerType.ArrowClosed },
      animated: true,
      data: { on: edge.on ?? 'success' },
      label: String(edge.on ?? 'success'),
      style: edgeStyle(),
    }));

    setNodes(loadedNodes);
    setEdges(loadedEdges);
    setSelectedNodeId(loadedNodes[0]?.id ?? '');
    setSelectedEdgeId('');
  }, [initialWorkflow, registeredAgents, setEdges, setNodes]);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((prev) =>
        addEdge(
          {
            ...connection,
            type: 'smoothstep',
            markerEnd: { type: MarkerType.ArrowClosed },
            animated: true,
            data: { on: 'success' },
            label: 'success',
            style: edgeStyle(),
          },
          prev,
        ),
      );
    },
    [setEdges],
  );

  const addRoleNode = useCallback(
    (roleDef: RoleNodeDef) => {
      const id = `n-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
      const newNode: Node<FlowNodeData> = {
        id,
        type: 'workflowNode',
        position: { x: 120 + nodes.length * 30, y: 80 + nodes.length * 20 },
        data: {
          title: roleDef.title,
          role: roleDef.role,
          workflow_type: roleDef.workflow_type,
          color: roleDef.color,
          agent: registeredAgents[0] ?? 'codex',
          params_text: DEFAULT_PARAMS,
        },
      };
      setNodes((prev) => [...prev, newNode]);
      setSelectedNodeId(id);
      setSelectedEdgeId('');
      setIsNodePickerOpen(false);
      setPickerSearch('');
    },
    [nodes.length, registeredAgents, setNodes],
  );

  const addNodeFromSelection = useCallback(() => {
    if (addCategory === 'role') {
      const selectedRoleDef = ROLE_NODES.find((item) => item.role === addRole) ?? ROLE_NODES[0];
      addRoleNode(selectedRoleDef);
      return;
    }

    // agent 카테고리는 agent_task 타입 노드로 생성한다.
    const id = `n-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
    const newNode: Node<FlowNodeData> = {
      id,
      type: 'workflowNode',
      position: { x: 120 + nodes.length * 30, y: 80 + nodes.length * 20 },
      data: {
        title: `${addAgent} agent`,
        role: 'planner',
        workflow_type: 'agent_task',
        color: '#A855F7',
        agent: addAgent,
        params_text: DEFAULT_PARAMS,
      },
    };
    setNodes((prev) => [...prev, newNode]);
    setSelectedNodeId(id);
    setSelectedEdgeId('');
  }, [addAgent, addCategory, addRole, addRoleNode, nodes.length, setNodes]);

  const updateSelectedNode = useCallback(
    (patch: Partial<FlowNodeData>) => {
      if (!selectedNodeId) return;
      setNodes((prev) =>
        prev.map((node) => {
          if (node.id !== selectedNodeId) return node;
          return { ...node, data: { ...node.data, ...patch } };
        }),
      );
    },
    [selectedNodeId, setNodes],
  );

  const updateLinkedAgent = useCallback(
    (agentNodeId: string) => {
      setLinkedAgentId(agentNodeId);
      if (!selectedNode || !selectedNodeIsRoleTask) return;
      const linkKey = roleLinkKey(selectedNode.data.role);
      setNodes((prev) =>
        prev.map((node) => {
          if (node.id !== selectedNode.id) return node;
          const raw = safeJsonParse(node.data.params_text);
          const current = isObject(raw) ? { ...raw } : {};
          current[linkKey] = agentNodeId;
          return {
            ...node,
            data: {
              ...node.data,
              params_text: JSON.stringify(current, null, 2),
            },
          };
        }),
      );
    },
    [selectedNode, selectedNodeIsRoleTask, setNodes],
  );

  const connectRoleAgentEdge = useCallback(() => {
    if (!selectedNode || !selectedNodeIsRoleTask || !linkedAgentId) return;
    const roleNodeId = selectedNode.id;
    const agentNodeId = linkedAgentId;
    const edgeTag = `llm:${selectedNode.data.role}`;

    setEdges((prev) => {
      const filtered = prev.filter((edge) => !(edge.target === roleNodeId && edge.data?.on === edgeTag));
      const exists = filtered.find((edge) => edge.source === agentNodeId && edge.target === roleNodeId && edge.data?.on === edgeTag);
      if (exists) return filtered;
      return [
        ...filtered,
        {
          id: `e-llm-${selectedNode.data.role}-${agentNodeId}-${roleNodeId}`,
          source: agentNodeId,
          target: roleNodeId,
          type: 'smoothstep',
          markerEnd: { type: MarkerType.ArrowClosed },
          animated: true,
          data: { on: edgeTag },
          label: `llm-${selectedNode.data.role}`,
          style: { stroke: '#8B5CF6', strokeWidth: 2, strokeDasharray: '6 4' },
        },
      ];
    });
  }, [linkedAgentId, selectedNode, selectedNodeIsRoleTask, setEdges]);

  const addRegisteredAgent = useCallback(() => {
    const candidate = newAgentName.trim().toLowerCase();
    if (!candidate) return;
    if (registeredAgents.includes(candidate)) {
      setNewAgentName('');
      return;
    }
    setRegisteredAgents((prev) => [...prev, candidate]);
    setNewAgentName('');
  }, [newAgentName, registeredAgents]);

  const addRegisteredLlmModel = useCallback(() => {
    const candidate = newLlmModelName.trim();
    if (!candidate) return;
    if (registeredLlmModels.includes(candidate)) {
      setNewLlmModelName('');
      return;
    }
    setRegisteredLlmModels((prev) => [...prev, candidate]);
    setNewLlmModelName('');
  }, [newLlmModelName, registeredLlmModels]);

  const removeRegisteredAgent = useCallback(
    (agent: string) => {
      // 최소 1개 에이전트는 남겨야 역할 매핑이 가능하다.
      if (registeredAgents.length <= 1) return;
      setRegisteredAgents((prev) => prev.filter((item) => item !== agent));
    },
    [registeredAgents.length],
  );

  const removeRegisteredLlmModel = useCallback(
    (model: string) => {
      if (registeredLlmModels.length <= 1) return;
      setRegisteredLlmModels((prev) => prev.filter((item) => item !== model));
    },
    [registeredLlmModels.length],
  );

  const removeSelectedNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((prev) => prev.filter((node) => node.id !== selectedNodeId));
    setEdges((prev) => prev.filter((edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId));
    setSelectedNodeId('');
  }, [selectedNodeId, setEdges, setNodes]);

  useEffect(() => {
    if (!selectedNode || !selectedNodeIsRoleTask) {
      setLinkedAgentId('');
      setLinkedLlmModel('');
      return;
    }
    const raw = safeJsonParse(selectedNode.data.params_text);
    if (!isObject(raw)) {
      setLinkedAgentId('');
      setLinkedLlmModel('');
      return;
    }
    const linkKey = roleLinkKey(selectedNode.data.role);
    const linked = typeof raw[linkKey] === 'string' ? String(raw[linkKey]) : '';
    setLinkedAgentId(linked);
    const modelKey = roleModelKey(selectedNode.data.role);
    const model = typeof raw[modelKey] === 'string' ? String(raw[modelKey]) : '';
    setLinkedLlmModel(model);
  }, [selectedNode, selectedNodeIsRoleTask]);

  const updateLinkedLlmModel = useCallback(
    (llmModel: string) => {
      setLinkedLlmModel(llmModel);
      if (!selectedNode || !selectedNodeIsRoleTask) return;
      const modelKey = roleModelKey(selectedNode.data.role);
      setNodes((prev) =>
        prev.map((node) => {
          if (node.id !== selectedNode.id) return node;
          const raw = safeJsonParse(node.data.params_text);
          const current = isObject(raw) ? { ...raw } : {};
          current[modelKey] = llmModel;
          return {
            ...node,
            data: {
              ...node.data,
              params_text: JSON.stringify(current, null, 2),
            },
          };
        }),
      );
    },
    [selectedNode, selectedNodeIsRoleTask, setNodes],
  );

  const removeSelectedEdge = useCallback(() => {
    if (!selectedEdgeId) return;
    setEdges((prev) => prev.filter((edge) => edge.id !== selectedEdgeId));
    setSelectedEdgeId('');
  }, [selectedEdgeId, setEdges]);

  const workflowPayload = useMemo(() => {
    return {
      workflow_id: workflowId.trim(),
      name: workflowName.trim() || workflowId.trim(),
      description: workflowDescription.trim(),
      version: 1,
      entry_node_id: nodes[0]?.id ?? '',
      nodes: nodes.map((node) => ({
        id: node.id,
        type: node.data.workflow_type,
        title: node.data.title,
        params: {
          role: node.data.role,
          agent: node.data.agent,
          extra: safeJsonParse(node.data.params_text),
        },
      })),
      edges: edges.map((edge) => ({
        from: edge.source,
        to: edge.target,
        on: (edge.data?.on as string | undefined) ?? 'success',
      })),
    };
  }, [edges, nodes, workflowDescription, workflowId, workflowName]);

  const validateWorkflow = useCallback(async () => {
    setSaveMsg('검증 중...');
    const response = await fetch('/api/workflows/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workflow: workflowPayload }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setSaveMsg(typeof payload.detail === 'string' ? payload.detail : '검증 실패');
      return;
    }
    if (payload.ok) {
      setSaveMsg('검증 성공: 저장 가능한 플로우입니다.');
    } else {
      const errors = Array.isArray(payload.errors) ? payload.errors.join(', ') : '알 수 없는 오류';
      setSaveMsg(`검증 실패: ${errors}`);
    }
  }, [workflowPayload]);

  const saveWorkflow = useCallback(async () => {
    setSaveMsg('저장 중...');
    const response = await fetch('/api/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workflow: workflowPayload, set_default: setAsDefault }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail);
      setSaveMsg(`저장 실패: ${detail}`);
      return;
    }
    setSaveMsg(`저장 완료: ${payload.workflow_id}`);
  }, [setAsDefault, workflowPayload]);

  return (
    <article className="box">
      <h2>단순 역할 기반 워크플로우</h2>
      <p className="hint">플래너 다음 디자이너(Codex) 단계를 포함해 역할 기반 흐름을 그립니다.</p>

      <div className="row">
        <input className="input" value={workflowId} onChange={(e) => setWorkflowId(e.target.value)} placeholder="workflow_id" />
        <input className="input" value={workflowName} onChange={(e) => setWorkflowName(e.target.value)} placeholder="워크플로우 이름" />
      </div>
      <textarea
        className="textarea"
        value={workflowDescription}
        onChange={(e) => setWorkflowDescription(e.target.value)}
        placeholder="설명"
      />

      <article className="box">
        <h3>CLI 에이전트 등록</h3>
        <p className="hint">여기서 등록한 에이전트가 아래 역할 선택 드롭다운에 표시됩니다.</p>
        <div className="row">
          <input
            className="input"
            placeholder="예: codex, gemini, claude, shell"
            value={newAgentName}
            onChange={(e) => setNewAgentName(e.target.value)}
          />
          <button className="btn" onClick={addRegisteredAgent}>에이전트 추가</button>
        </div>
        <div className="wfAgentList">
          {registeredAgents.map((agent) => (
            <span key={agent} className="wfAgentChip">
              <strong>{agent}</strong>
              <button
                className="wfAgentRemove"
                onClick={() => removeRegisteredAgent(agent)}
                disabled={registeredAgents.length <= 1}
                title="에이전트 삭제"
              >
                x
              </button>
            </span>
          ))}
        </div>
      </article>

      <article className="box">
        <h3>LLM 모델 등록</h3>
        <p className="hint">LLM 모델은 에이전트와 분리되어 역할 노드에 독립 매핑됩니다.</p>
        <div className="row">
          <input
            className="input"
            placeholder="예: gemini-3-flash-preview"
            value={newLlmModelName}
            onChange={(e) => setNewLlmModelName(e.target.value)}
          />
          <button className="btn" onClick={addRegisteredLlmModel}>LLM 모델 추가</button>
        </div>
        <div className="wfAgentList">
          {registeredLlmModels.map((model) => (
            <span key={model} className="wfAgentChip">
              <strong>{model}</strong>
              <button
                className="wfAgentRemove"
                onClick={() => removeRegisteredLlmModel(model)}
                disabled={registeredLlmModels.length <= 1}
                title="LLM 모델 삭제"
              >
                x
              </button>
            </span>
          ))}
        </div>
      </article>

      <div className="row">
        <label className="hint">
          <input type="checkbox" checked={setAsDefault} onChange={(e) => setSetAsDefault(e.target.checked)} />
          {' '}기본 워크플로우로 설정
        </label>
        <button className="btn" onClick={validateWorkflow}>검증</button>
        <button className="btn" onClick={saveWorkflow}>워크플로우 저장</button>
        {saveMsg ? <span className="hint">{saveMsg}</span> : null}
      </div>

      <div className="wfLayout">
        <aside className="wfPanel">
          <h3 className="wfTitle">노드 관리</h3>
          <p className="hint">카테고리와 agent/역할만 선택해서 노드를 추가합니다.</p>
          <label className="hint">
            카테고리
            <select className="select" value={addCategory} onChange={(e) => setAddCategory(e.target.value as 'agent' | 'role')}>
              <option value="agent">agent</option>
              <option value="role">역할</option>
            </select>
          </label>
          {addCategory === 'role' ? (
            <label className="hint">
              역할
              <select className="select" value={addRole} onChange={(e) => setAddRole(e.target.value as RoleType)}>
                {ROLE_NODES.map((roleDef) => (
                  <option key={roleDef.role} value={roleDef.role}>{roleDef.title}</option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <label className="hint">
                agent
                <select className="select" value={addAgent} onChange={(e) => setAddAgent(e.target.value)}>
                  {registeredAgents.map((agent) => (
                    <option key={agent} value={agent}>{agent}</option>
                  ))}
                </select>
              </label>
              <p className="hint">agent 선택 시 agent_task 타입 노드가 생성됩니다.</p>
            </>
          )}
          <button className="btn" onClick={addNodeFromSelection}>+ 노드 추가</button>

          <div className="wfNodeList">
            {nodes.map((node) => (
              <button
                key={node.id}
                className={`wfNodeItem ${selectedNodeId === node.id ? 'active' : ''}`}
                onClick={() => {
                  setSelectedNodeId(node.id);
                  setSelectedEdgeId('');
                }}
              >
                <span className="wfNodeDot" style={{ backgroundColor: node.data.color }} />
                <span>{node.data.title}</span>
                <span className="wfNodeMeta">{node.data.agent}</span>
              </button>
            ))}
          </div>
        </aside>

        <div className="wfCanvasWrap">
          <button
            className="wfCanvasAddBtn"
            onClick={() => setIsNodePickerOpen((prev) => !prev)}
            aria-label="노드 추가 패널 열기"
            title="노드 추가"
          >
            +
          </button>

          {isNodePickerOpen ? (
            <aside className="wfAddPanel" role="dialog" aria-label="노드 추가">
              <div className="wfAddPanelHead">
                <strong>What happens next?</strong>
                <button
                  className="btn"
                  onClick={() => setIsNodePickerOpen(false)}
                  aria-label="노드 추가 패널 닫기"
                >
                  닫기
                </button>
              </div>

              <input
                className="input"
                placeholder="Search nodes..."
                value={pickerSearch}
                onChange={(e) => setPickerSearch(e.target.value)}
              />

              <div className="wfAddCategoryList">
                {NODE_PICKER_CATEGORIES.map((item) => (
                  <button
                    key={item.key}
                    className={`wfAddCategoryItem ${pickerCategory === item.key ? 'active' : ''}`}
                    onClick={() => setPickerCategory(item.key)}
                  >
                    <span>
                      <strong>{item.title}</strong>
                      <small>{item.description}</small>
                    </span>
                    <span aria-hidden>›</span>
                  </button>
                ))}
              </div>

              <div className="wfAddNodeList">
                {availablePickerNodes.length === 0 ? (
                  <p className="hint">선택한 카테고리에서 추가 가능한 노드가 없습니다.</p>
                ) : (
                  availablePickerNodes.map((roleDef) => (
                    <button
                      key={roleDef.role}
                      className="wfAddNodeItem"
                      onClick={() => addRoleNode(roleDef)}
                    >
                      <span className="wfNodeDot" style={{ backgroundColor: roleDef.color }} />
                      <span>
                        <strong>{roleDef.title}</strong>
                        <small>{ROLE_NODE_HINTS[roleDef.role]}</small>
                      </span>
                    </button>
                  ))
                )}
              </div>
            </aside>
          ) : null}

          <div className="wfCanvas">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, node) => {
                setSelectedNodeId(node.id);
                setSelectedEdgeId('');
              }}
              onEdgeClick={(_, edge) => {
                setSelectedEdgeId(edge.id);
                setSelectedNodeId('');
              }}
              onPaneClick={() => {
                setSelectedNodeId('');
                setSelectedEdgeId('');
              }}
              fitView
            >
              <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} />
              <MiniMap />
              <Controls />
            </ReactFlow>
          </div>
        </div>

        <aside className="wfPanel">
          <h3 className="wfTitle">노드/연결선 관리</h3>

          {!selectedNode ? (
            <p className="hint">노드를 선택하면 상세 설정이 표시됩니다.</p>
          ) : (
            <>
              <label className="hint">노드 제목</label>
              <input
                className="input"
                value={selectedNode.data.title}
                onChange={(e) => updateSelectedNode({ title: e.target.value })}
              />

              <label className="hint">역할</label>
              <input className="input" value={selectedNode.data.role} disabled />

              <label className="hint">워크플로우 타입</label>
              <input className="input" value={selectedNode.data.workflow_type} disabled />

              <label className="hint">에이전트</label>
              <input className="input" value={selectedNode.data.agent} disabled />

              <label className="hint">노드 추가 설정(JSON)</label>
              <textarea
                className="textarea"
                value={selectedNode.data.params_text}
                onChange={(e) => updateSelectedNode({ params_text: e.target.value })}
              />

              {selectedNodeIsRoleTask ? (
                <>
                  <label className="hint">LLM Agent 노드 매핑</label>
                  <select
                    className="select"
                    value={linkedAgentId}
                    onChange={(e) => updateLinkedAgent(e.target.value)}
                  >
                    <option value="">선택 안 함</option>
                    {agentNodes.map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.data.title} ({node.id})
                      </option>
                    ))}
                  </select>
                  <button className="btn" onClick={connectRoleAgentEdge} disabled={!linkedAgentId}>
                    {selectedNode.data.role}에 Agent 연결
                  </button>
                  <label className="hint">LLM 모델 매핑</label>
                  <select
                    className="select"
                    value={linkedLlmModel}
                    onChange={(e) => updateLinkedLlmModel(e.target.value)}
                  >
                    <option value="">선택 안 함</option>
                    {registeredLlmModels.map((model) => (
                      <option key={model} value={model}>{model}</option>
                    ))}
                  </select>
                  <p className="hint">n8n의 AI Agent - LLM 연결처럼 {selectedNode.data.role}에 agent_task 노드를 붙입니다.</p>
                </>
              ) : null}

              <button className="btn btnDanger" onClick={removeSelectedNode}>선택 노드 삭제</button>
            </>
          )}

          <hr className="wfDivider" />
          <h3 className="wfTitle">연결선 관리</h3>
          <button className="btn btnDanger" onClick={removeSelectedEdge} disabled={!selectedEdgeId}>
            선택 연결선 삭제
          </button>
          {selectedEdgeId ? <p className="hint">선택된 연결선: {selectedEdgeId}</p> : null}

          <div className="wfEdgeList">
            {edges.map((edge) => (
              <button
                key={edge.id}
                className={`wfEdgeItem ${selectedEdgeId === edge.id ? 'active' : ''}`}
                onClick={() => {
                  setSelectedEdgeId(edge.id);
                  setSelectedNodeId('');
                }}
              >
                <span>{edge.source}{' -> '}{edge.target}</span>
                <span className="wfNodeMeta">{String(edge.data?.on ?? 'success')}</span>
              </button>
            ))}
          </div>
        </aside>
      </div>

      <details>
        <summary className="hint">저장될 워크플로우 JSON 보기</summary>
        <pre className="wfPreview">{JSON.stringify(workflowPayload, null, 2)}</pre>
      </details>
    </article>
  );
}

function safeJsonParse(text: string): unknown | null {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isRoleNodeMappable(role: RoleType): boolean {
  return role === 'planner' || role === 'designer' || role === 'coder' || role === 'reviewer' || role === 'escalator';
}

function roleLinkKey(role: RoleType): string {
  if (role === 'planner') return 'planner_llm_agent_node_id';
  if (role === 'designer') return 'designer_llm_agent_node_id';
  if (role === 'coder') return 'coder_llm_agent_node_id';
  if (role === 'escalator') return 'escalator_llm_agent_node_id';
  return 'reviewer_llm_agent_node_id';
}

function roleModelKey(role: RoleType): string {
  if (role === 'planner') return 'planner_llm_model';
  if (role === 'designer') return 'designer_llm_model';
  if (role === 'coder') return 'coder_llm_model';
  if (role === 'escalator') return 'escalator_llm_model';
  return 'reviewer_llm_model';
}
