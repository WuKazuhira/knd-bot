// Package router 负责把 OneBot 消息解析成 pjsk 指令并分发到对应处理器。
//
// 对齐 nonebot on_command 语义：支持命令起始符（COMMAND_START）、别名、
// 以及 cn/tw 服务器前缀（cnsk/twsk 等）。只处理被 Go 接管（owned）的指令，
// 与 Python 侧命令所有权互斥。
package router

import (
	"context"
	"regexp"
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
	// RegexGroups 是正则触发时的捕获组（[0] 为整体匹配），命令触发时为 nil。
	RegexGroups []string
}

// Handler 处理一个指令请求，返回要发送的消息（nil 表示不回复）。
type Handler func(ctx context.Context, req Request) *onebot.ActionRequest

// command 是一条已注册指令。
type command struct {
	name               string // 规范指令名
	aliases            []string
	handler            Handler
	allowNumericSuffix bool // 允许数字紧接在触发词后作为参数
}

// regexRoute 是一条正则触发的指令（用于 on_regex 型指令，如抽卡）。
type regexRoute struct {
	name    string
	re      *regexp.Regexp
	handler Handler
}

// Router 保存指令注册表与命令起始符。
type Router struct {
	starts    []string            // 命令起始符，如 ["/", ""]
	commands  map[string]*command // 触发词（小写，含别名与服务器前缀展开）-> 指令
	ordered   []string            // 触发词按长度降序，保证最长匹配
	ownership Ownership           // 命令所有权：只处理被 Go 接管的指令
	regexes   []regexRoute        // 正则触发指令
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
	r.register(name, aliases, handler, false)
}

// RegisterNumericSuffix 注册一条允许数字紧接在触发词后的指令。
// 例如注册 sk 后可匹配 sk100，并把 100 作为 Request.Arg；其它非数字后缀
// 仍然遵守普通命令的分隔要求，避免把 skill 等相似文本误认为命令。
func (r *Router) RegisterNumericSuffix(name string, aliases []string, handler Handler) {
	r.register(name, aliases, handler, true)
}

func (r *Router) register(name string, aliases []string, handler Handler, allowNumericSuffix bool) {
	cmd := &command{
		name:               name,
		aliases:            aliases,
		handler:            handler,
		allowNumericSuffix: allowNumericSuffix,
	}
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

// RegisterRegex 注册一条正则触发指令（用于 on_regex 型，如抽卡）。
// name 为命令所有权 key；pattern 为正则（对整条消息文本匹配）。
func (r *Router) RegisterRegex(name, pattern string, handler Handler) {
	r.regexes = append(r.regexes, regexRoute{
		name:    name,
		re:      regexp.MustCompile(pattern),
		handler: handler,
	})
}

// TriggerCount 返回当前注册的命令触发词数量（包含别名和区服前缀展开）。
func (r *Router) TriggerCount() int { return len(r.commands) }

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

// serverOfKey 从命中的触发词推断服务器：仅当剥掉 cn/tw 前缀后剩余部分确实是该
// 命令的注册触发词（规范名或别名）时，才认定为分服前缀；否则视为 JP。
// 这避免把本身以 cn/tw 开头的命令名（如 cnmsr启用）误判成 CN 服前缀。
func (r *Router) serverOfKey(key string, cmd *command) ServerType {
	server, rest := serverOf(key)
	if server == ServerJP {
		return ServerJP
	}
	// rest 必须是该命令的某个无前缀触发词，否则前缀属于命令名本身。
	restLower := strings.ToLower(rest)
	if restLower == strings.ToLower(cmd.name) {
		return server
	}
	for _, a := range cmd.aliases {
		if restLower == strings.ToLower(a) {
			return server
		}
	}
	return ServerJP
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
		cmd := r.commands[key]
		// 普通触发词后必须是分隔（空白）或结束，避免 "sk" 命中 "skill"；
		// 明确声明支持数字后缀的查询命令则允许 "sk100" 这类写法。
		rest := body[len(key):]
		if rest != "" && !isSpace(rest[0]) && !(cmd.allowNumericSuffix && isDigit(rest[0])) {
			continue
		}
		// 命令所有权：未被 Go 接管的指令交给 Python 处理。
		if !r.ownership.Owns(cmd.name) {
			return Request{}, nil, false
		}
		server := r.serverOfKey(key, cmd)
		return Request{
			Event:   event,
			Command: cmd.name,
			RawCmd:  key,
			Server:  server,
			Arg:     strings.TrimSpace(rest),
		}, cmd.handler, true
	}

	// 命令未命中，尝试正则触发指令。
	for i := range r.regexes {
		rr := &r.regexes[i]
		groups := rr.re.FindStringSubmatch(body)
		if groups == nil {
			continue
		}
		if !r.ownership.Owns(rr.name) {
			return Request{}, nil, false
		}
		return Request{
			Event:       event,
			Command:     rr.name,
			RawCmd:      rr.name,
			Server:      ServerJP, // 具体服务器由 handler 从 RegexGroups 解析
			Arg:         strings.TrimSpace(body),
			RegexGroups: groups,
		}, rr.handler, true
	}
	return Request{}, nil, false
}

func isSpace(b byte) bool {
	return b == ' ' || b == '\t' || b == '\n' || b == '\r'
}

func isDigit(b byte) bool {
	return b >= '0' && b <= '9'
}
