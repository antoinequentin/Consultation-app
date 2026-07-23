{{- define "boussole.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "boussole.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "boussole.labels" -}}
app.kubernetes.io/name: {{ include "boussole.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "boussole.selectorLabels" -}}
app.kubernetes.io/name: {{ include "boussole.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
