export interface EventReceipt { status: string; id: string | null; errorCode: string | null; message: string }

export function eventReceipt(data: unknown, headers: unknown): EventReceipt | null {
  const body = data && typeof data === 'object' ? data as Record<string, unknown> : {}
  const values = headers && typeof headers === 'object' ? headers as Record<string, unknown> : {}
  const status = body.event_status ?? values['x-event-status']
  if (typeof status !== 'string') return null
  const id = body.event_id ?? values['x-event-id']
  const errorCode = body.event_error_code ?? values['x-event-error-code']
  const messages: Record<string, string> = {
    published: '业务操作已保存，Kafka 已确认接收；这不代表消费者已完成消费。请到实验台刷新事件轨迹。',
    failed: '业务操作已保存，但 Kafka 事件发送失败。不要反复提交业务操作来假装重放；请检查服务和事件轨迹。',
    disabled: '业务操作已保存；Kafka 事件未启用，不会产生实时兴趣更新。',
  }
  return { status, id: typeof id === 'string' ? id : null, errorCode: typeof errorCode === 'string' ? errorCode : null,
    message: messages[status] || '业务操作已保存，但事件状态无法识别，请检查服务端原始回执。' }
}
