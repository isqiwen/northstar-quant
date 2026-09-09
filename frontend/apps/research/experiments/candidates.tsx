"use client";
import { query, mutate } from "../api/client";
import { App, Button, Card, Form, Input, Select } from "antd";
import { useRouter } from "next/navigation";
import { useData } from "../../../shared/data";
import { ConfigurationSelect } from "../../../shared/forms";
import { Heading, Failure } from "../../../shared/ui";
export default function Candidates() {
  const cfg = useData(query("/api/configurations"));
  const runs = useData(query("/api/runs"));
  const navigate = useRouter().push;
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const selectedConfig = Form.useWatch("configuration_id", form);
  const options = (runs.data || []).map((r) => ({
    value: r.run_id,
    label: r.run_id.slice(0, 12),
  }));
  return (
    <>
      <Heading
        title="发布候选"
        description="固定配置与研究证据，发布不授予交易权限。"
      />
      <Failure error={cfg.error || runs.error} />
      <Card title="登记固定策略版本">
        <Form
          name="version"
          form={form}
          layout="vertical"
          onFinish={async (v) => {
            try {
              const result = await mutate("/api/strategy-versions", v);
              navigate(`/strategy-versions/${result.version_id}`);
            } catch (e) {
              message.error((e as Error).message);
            }
          }}
        >
          <div className="grid-two">
            <Form.Item
              name="name"
              label="策略版本名称"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <ConfigurationSelect rows={cfg.data || []} />
          </div>
          <Form.Item
            name="run_ids"
            label="引用此配置的研究结果"
            rules={[{ required: true }]}
          >
            <Select mode="multiple" options={options} />
          </Form.Item>
          <Button disabled={!selectedConfig} htmlType="submit">
            登记固定版本
          </Button>
        </Form>
      </Card>
    </>
  );
}
