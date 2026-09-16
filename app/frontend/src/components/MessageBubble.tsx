import type { ChatMessage } from "@/lib/types";

interface Props {
  message: ChatMessage;
}

export function MessageBubble({ message }: Props) {
  if (message.role === "system") {
    return <div className="chat-system-message">{message.content}</div>;
  }

  const isUser = message.role === "user";
  return (
    <div className={`chat-message-row ${isUser ? "is-user" : ""}`}>
      <div className="chat-message-bubble">
        <span className="chat-message-role">
          {isUser ? "あなた" : "エージェント"}
        </span>
        <p>{message.content}</p>
      </div>
    </div>
  );
}
