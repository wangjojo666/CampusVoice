function dateTime(value) {
  if (!value) return "未设置";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间格式无效";
  const pad = (part) => String(part).padStart(2, "0");
  return (
    date.getFullYear() +
    "-" +
    pad(date.getMonth() + 1) +
    "-" +
    pad(date.getDate()) +
    " " +
    pad(date.getHours()) +
    ":" +
    pad(date.getMinutes())
  );
}

function friendlyError(reason) {
  return reason && reason.message ? reason.message : "操作失败，请稍后重试";
}

module.exports = { dateTime, friendlyError };
