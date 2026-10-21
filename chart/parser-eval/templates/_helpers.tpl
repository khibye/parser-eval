{{- define "parser-eval.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "parser-eval.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "parser-eval.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "parser-eval.secretName" -}}
{{- if .Values.secrets.sealedSecret.enabled -}}
{{- .Values.secrets.name -}}
{{- else -}}
{{- required "Set secrets.existingSecret, or enable secrets.sealedSecret" .Values.secrets.existingSecret -}}
{{- end -}}
{{- end -}}

{{- define "parser-eval.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-data" (include "parser-eval.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "parser-eval.proxyUrl" -}}
{{- if .Values.vlmProxyPublicUrl -}}
{{- .Values.vlmProxyPublicUrl -}}
{{- else -}}
{{- printf "http://%s.%s.svc:%v/v1" (include "parser-eval.fullname" .) .Release.Namespace .Values.service.proxyPort -}}
{{- end -}}
{{- end -}}
