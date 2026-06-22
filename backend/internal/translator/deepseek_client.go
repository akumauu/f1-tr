package translator

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

const systemPrompt = `你是一位专业的 F1 赛事翻译和分析专家。你的任务是将 F1 团队电台消息从英文翻译成中文，并进行结构化分析。

## 翻译要求
1. 专业术语使用括号标注原文，如："进站（Box）"
2. 保留原始情感和语气强度
3. 缩写必须展开并翻译
4. 如果原文包含脏话或强烈情绪词汇，翻译时保留语气但适当处理
5. 车手名字保留英文原名

## 输出格式（严格 JSON）
{
  "transcript_zh": "中文翻译全文",
  "intent": "STRATEGY|WARNING|TECHNICAL|EMOTION|INFO|ORDER",
  "sentiment": "POSITIVE|NEGATIVE|NEUTRAL|URGENT",
  "key_entities": {
    "action": "动作关键词或null",
    "tire": "轮胎类型或null",
    "lap": null,
    "gap": "差距信息或null",
    "driver_mentioned": "提到的车手或null"
  }
}

## Intent 分类说明
- STRATEGY: 进站策略、轮胎选择、比赛战术
- WARNING: 黄旗、赛道状况、前方事故、罚时警告
- TECHNICAL: 引擎模式、ERS设置、车辆损伤、机械问题
- EMOTION: 庆祝、沮丧、愤怒等情感表达
- INFO: 间距播报、位置变化、天气信息
- ORDER: 车队指令、让位、防守指令

`

// DeepSeekClient wraps the DeepSeek chat completions API.
type DeepSeekClient struct {
	apiKey     string
	baseURL    string
	model      string
	httpClient *http.Client
}

// NewDeepSeekClient creates a new DeepSeek API client.
func NewDeepSeekClient(apiKey, baseURL, model string) *DeepSeekClient {
	if baseURL == "" {
		baseURL = "https://api.deepseek.com"
	}
	if model == "" {
		model = "deepseek-chat"
	}
	return &DeepSeekClient{
		apiKey:  apiKey,
		baseURL: baseURL,
		model:   model,
		httpClient: &http.Client{
			Timeout: 60 * time.Second,
		},
	}
}

// TranslationResult holds the structured output from DeepSeek.
type TranslationResult struct {
	TranscriptZH string                 `json:"transcript_zh"`
	Intent       string                 `json:"intent"`
	Sentiment    string                 `json:"sentiment"`
	KeyEntities  map[string]interface{} `json:"key_entities"`
}

// chatRequest is the OpenAI-compatible request body.
type chatRequest struct {
	Model          string        `json:"model"`
	Messages       []chatMessage `json:"messages"`
	Temperature    float64       `json:"temperature"`
	ResponseFormat *respFormat   `json:"response_format,omitempty"`
}

type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type respFormat struct {
	Type string `json:"type"`
}

// chatResponse is the OpenAI-compatible response body.
type chatResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

// Translate sends a team radio transcript to DeepSeek for translation and analysis.
func (c *DeepSeekClient) Translate(ctx context.Context, transcript string, driverAcronym string) (*TranslationResult, error) {
	fullSystemPrompt := systemPrompt + BuildGlossaryPrompt()

	userPrompt := fmt.Sprintf("请翻译并分析以下 F1 团队电台消息（车手: %s）：\n\n\"%s\"", driverAcronym, transcript)

	reqBody := chatRequest{
		Model: c.model,
		Messages: []chatMessage{
			{Role: "system", Content: fullSystemPrompt},
			{Role: "user", Content: userPrompt},
		},
		Temperature:    0.3,
		ResponseFormat: &respFormat{Type: "json_object"},
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	// Retry with backoff
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			wait := time.Duration(2<<uint(attempt-1)) * time.Second
			select {
			case <-time.After(wait):
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}

		req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/chat/completions", bytes.NewReader(bodyBytes))
		if err != nil {
			return nil, fmt.Errorf("create request: %w", err)
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+c.apiKey)

		resp, err := c.httpClient.Do(req)
		if err != nil {
			lastErr = fmt.Errorf("http request: %w", err)
			continue
		}

		respBody, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			lastErr = fmt.Errorf("read response: %w", err)
			continue
		}

		if resp.StatusCode == 429 {
			lastErr = fmt.Errorf("rate limited")
			continue
		}
		if resp.StatusCode != 200 {
			lastErr = fmt.Errorf("status %d: %s", resp.StatusCode, string(respBody))
			continue
		}

		var chatResp chatResponse
		if err := json.Unmarshal(respBody, &chatResp); err != nil {
			return nil, fmt.Errorf("unmarshal response: %w", err)
		}

		if chatResp.Error != nil {
			return nil, fmt.Errorf("api error: %s", chatResp.Error.Message)
		}

		if len(chatResp.Choices) == 0 {
			return nil, fmt.Errorf("no choices in response")
		}

		var result TranslationResult
		content := chatResp.Choices[0].Message.Content
		if err := json.Unmarshal([]byte(content), &result); err != nil {
			return nil, fmt.Errorf("parse translation result: %w (content: %s)", err, content)
		}

		return &result, nil
	}

	return nil, fmt.Errorf("all retries failed: %w", lastErr)
}
