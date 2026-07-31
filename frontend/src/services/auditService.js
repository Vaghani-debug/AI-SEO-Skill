export async function submitAuditRequest(url) {
  await new Promise((resolve) => {
    window.setTimeout(resolve, 700);
  });

  return { url };
}
