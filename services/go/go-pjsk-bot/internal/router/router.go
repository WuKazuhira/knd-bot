package router

import (
	"context"
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
)

type ServerType int

const (
	ServerJP ServerType = 0
	ServerTW ServerType = 1
	ServerCN ServerType = 2
)

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

type Request struct {
	Event       onebot.MessageEvent
	Command     string
	RawCmd      string
	Server      ServerType
	Arg         string
	RegexGroups []string
}

type Handler func(ctx context.Context, req Request) *onebot.ActionRequest

type command struct {
	name               string
	aliases            []string
	handler            Handler
	allowNumericSuffix bool
}

type regexRoute struct {
	name    string
	re      *regexp.Regexp
	handler Handler
}

type Router struct {
	starts      []string
	commands    map[string]*command
	ordered     []string
	byFirstByte [256][]string
	ownership   Ownership
	regexes     []regexRoute
}

func New(starts []string, ownership Ownership) *Router {
	if len(starts) == 0 {
		starts = []string{""}
	}
	// 允许无起始符时补上常见前缀符号
	starts = append([]string(nil), starts...)
	for _, start := range starts {
		if start == "" {
			starts = append(starts, "!", "！", "/", "／", ".")
			break
		}
	}
	sort.SliceStable(starts, func(i, j int) bool { return len(starts[i]) > len(starts[j]) })
	return &Router{starts: starts, commands: make(map[string]*command), ownership: ownership}
}

func (r *Router) Register(name string, aliases []string, handler Handler) {
	r.register(name, aliases, handler, false)
}

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
			if previous, ok := r.commands[key]; ok && previous.name != name {
				panic("router: conflicting trigger " + key + ": " + previous.name + " / " + name)
			}
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
	var buckets [256][]string
	for _, k := range r.ordered {
		if k != "" {
			b := k[0]
			buckets[b] = append(buckets[b], k)
		}
	}
	r.byFirstByte = buckets
}

func (r *Router) RegisterRegex(name, pattern string, handler Handler) {
	// 正则统一忽略大小写且必须锚定开头
	if !strings.HasPrefix(strings.TrimPrefix(pattern, "(?i)"), "^") {
		panic("router: regex must be start-anchored: " + name)
	}
	r.regexes = append(r.regexes, regexRoute{
		name:    name,
		re:      regexp.MustCompile("(?i)" + pattern),
		handler: handler,
	})
}

func (r *Router) TriggerCount() int { return len(r.commands) }

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

// 只有去掉 cn/tw 后仍匹配已注册触发词才算分服前缀，避免把 cnmsr启用 之类误拆
func (r *Router) serverOfKey(key string, cmd *command) ServerType {
	server, rest := serverOf(key)
	if server == ServerJP {
		return ServerJP
	}
	restLower := strings.ToLower(rest)
	if restLower == strings.ToLower(cmd.name) {
		return server
	}
	// 兼容旧的 cn_cardinfo/tw_cardinfo 下划线写法
	if restLower == "_"+strings.ToLower(cmd.name) {
		for _, alias := range cmd.aliases {
			if strings.ToLower(alias) == key {
				return server
			}
		}
	}
	for _, a := range cmd.aliases {
		if restLower == strings.ToLower(a) {
			return server
		}
	}
	return ServerJP
}

func (r *Router) Match(event onebot.MessageEvent) (Request, Handler, bool) {
	text := strings.TrimSpace(event.Message.PlainText())
	if text == "" {
		return Request{}, nil, false
	}
	body := text
	matchedStart := false
	for _, s := range r.starts {
		if s == "" {
			matchedStart = true
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
	// 按首字节分桶做最长匹配，避免普通群聊消息全表扫描
	var candidates []string
	if body != "" {
		first, _ := utf8.DecodeRuneInString(body)
		candidates = r.byFirstByte[string(unicode.ToLower(first))[0]]
	}
	for _, key := range candidates {
		offset, matches := commandPrefixOffset(body, key)
		if !matches {
			continue
		}
		cmd := r.commands[key]
		// 触发词后必须有空白或结束，防止 sk 误触发 skill；允许数字后缀的命令除外
		rest := body[offset:]
		allowNumericSuffix := false
		if cmd.allowNumericSuffix && rest != "" {
			allowNumericSuffix = isDigit(rest[0]) || (rest[0] == '-' && len(rest) > 1 && isDigit(rest[1]))
		}
		if rest != "" && !isSpace(rest[0]) && !allowNumericSuffix {
			continue
		}
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

	for i := range r.regexes {
		rr := &r.regexes[i]
		groups := rr.re.FindStringSubmatch(body)
		if groups == nil {
			continue
		}
		if !r.ownership.Owns(rr.name) {
			return Request{}, nil, false
		}
		// 把区服捕获组转成小写，下游 handler 按小写分服
		if len(groups) > 1 {
			prefix := strings.ToLower(groups[1])
			if prefix == "cn" || prefix == "tw" || prefix == "jp" {
				groups[1] = prefix
			}
		}
		return Request{
			Event:       event,
			Command:     rr.name,
			RawCmd:      rr.name,
			Server:      ServerJP,
			Arg:         strings.TrimSpace(body),
			RegexGroups: groups,
		}, rr.handler, true
	}
	return Request{}, nil, false
}

func commandPrefixOffset(body, lowerKey string) (int, bool) {
	offset := 0
	for _, want := range lowerKey {
		if offset >= len(body) {
			return 0, false
		}
		got, size := utf8.DecodeRuneInString(body[offset:])
		if unicode.ToLower(got) != want {
			return 0, false
		}
		offset += size
	}
	return offset, true
}

func isSpace(b byte) bool {
	return b == ' ' || b == '\t' || b == '\n' || b == '\r'
}

func isDigit(b byte) bool {
	return b >= '0' && b <= '9'
}
