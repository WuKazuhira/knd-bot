// Package router 负责把 OneBot 消息解析成 pjsk 指令并分发到对应处理器。
//
// 对齐 nonebot on_command 语义：支持命令起始符（COMMAND_START）、别名、
// 以及 cn/tw 服务器前缀（cnsk/twsk 等）。只处理被 Go 接管（owned）的指令，
// 与 Python 侧命令所有权互斥。
package router

import (
	"context"
	"sort"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
)

// ServerType 对齐 Python pjsk_type：0=日服 1=台服 2=国服。
type ServerType int

const (
	ServerJP ServerType = 0
	ServerTW ServerType = 1
	ServerCN ServerType = 2
)

// Name 返回中文服务器名。
func (s ServerType) Name() string {
	switch s {
	case ServerCN:
		return "国服"
	case ServerTW:
		return "台服"
	default:
		return "日服"
	}
}

// Request 是解析后的指令上下文，传给业务处理器。
type Request struct {
	Event   onebot.MessageEvent
	Command string     // 归一化后的指令名（去掉 cn/tw 前缀），如 "sk"
	RawCmd  string     // 原始命中的指令名（含前缀），如 "cnsk"
	Server  ServerType // 由前缀推断的服务器
	Arg     string     // 指令后的纯文本参数（已 trim）
}

// Handler 处理一个指令请求，返回要发送的消息（nil 表示不回复）。
type Handler func(ctx context.Context, req Request) *onebot.ActionRequest

// command 是一条已注册指令。
type command struct {
	name    string // 规范指令名
	aliases []string
	handler Handler
}

// Router 保存指令注册表与命令起始符。
type Router struct {
	starts    []string            // 命令起始符，如 ["/", ""]
	commands  map[string]*command // 触发词（小写，含别名与服务器前缀展开）-> 指令
	ordered   []string            // 触发词按长度降序，保证最长匹配
	ownership Ownership           // 命令所有权：只处理被 Go 接管的指令
}

// New 创建路由器。starts 为命令起始符集合（如 ["/",""] 表示可带或不带 /）；
// ownership 决定哪些指令由本服务接管（未接管的指令 Match 返回 false，交给 Python）。
func New(starts []string, ownership Ownership) *Router {
	if len(starts) == 0 {
		starts = []string{""}
	}
	return &Router{starts: starts, commands: make(map[string]*command), ownership: ownership}
}

// Register 注册一条指令。name 为规范名，aliases 为别名（都不含 cn/tw 前缀）。
// 会自动展开 cn/tw 前缀触发词。
func (r *Router) Register(name string, aliases []string, handler Handler) {
	cmd := &command{name: name, aliases: aliases, handler: handler}
	triggers := append([]string{name}, aliases...)
	for _, t := range triggers {
		for _, prefix := range []string{"", "cn", "tw"} {
			key := strings.ToLower(prefix + t)
			r.commands[key] = cmd
		}
	}
	r.rebuild()
}

func (r *Router) rebuild() {
	r.ordered = r.ordered[:0]
	for k := range r.commands {
		r.ordered = append(r.ordered, k)
	}
	sort.Slice(r.ordered, func(i, j int) bool {
		return len(r.ordered[i]) > len(r.ordered[j])
	})
}

// serverOf 从触发词前缀推断服务器，并返回去前缀后的规范触发词。
func serverOf(trigger string) (ServerType, string) {
	lower := strings.ToLower(trigger)
	switch {
	case strings.HasPrefix(lower, "cn"):
		return ServerCN, trigger[2:]
	case strings.HasPrefix(lower, "tw"):
		return ServerTW, trigger[2:]
	default:
		return ServerJP, trigger
	}
}

// Match 解析消息文本，命中则返回 (请求, 处理器, true)。
func (r *Router) Match(event onebot.MessageEvent) (Request, Handler, bool) {
	text := strings.TrimSpace(event.Message.PlainText())
	if text == "" {
		return Request{}, nil, false
	}
	// 去掉命令起始符
	body := text
	matchedStart := false
	for _, s := range r.starts {
		if s == "" {
			matchedStart = true // 允许无起始符
			continue
		}
		if strings.HasPrefix(text, s) {
			body = strings.TrimSpace(text[len(s):])
			matchedStart = true
			break
		}
	}
	if !matchedStart {
		return Request{}, nil, false
	}
	// 最长匹配触发词
	lowerBody := strings.ToLower(body)
	for _, key := range r.ordered {
		if !strings.HasPrefix(lowerBody, key) {
			continue
		}
		// 触发词后必须是分隔（空白）或结束，避免 "sk" 命中 "skill"
		rest := body[len(key):]
		if rest != "" && !isSpace(rest[0]) {
			continue
		}
		cmd := r.commands[key]
		// 命令所有权：未被 Go 接管的指令交给 Python 处理。
		if !r.ownership.Owns(cmd.name) {
			return Request{}, nil, false
		}
		server, _ := serverOf(key)
		return Request{
			Event:   event,
			Command: cmd.name,
			RawCmd:  key,
			Server:  server,
			Arg:     strings.TrimSpace(rest),
		}, cmd.handler, true
	}
	return Request{}, nil, false
}

func isSpace(b byte) bool {
	return b == ' ' || b == '\t' || b == '\n' || b == '\r'
}
