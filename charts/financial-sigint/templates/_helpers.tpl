{{- define "financial-sigint.fullname" -}}
{{ .Chart.Name }}
{{- end -}}

{{- define "financial-sigint.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "financial-sigint.componentLabels" -}}
{{ include "financial-sigint.labels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}
