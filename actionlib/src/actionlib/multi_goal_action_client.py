from actionlib import SimpleActionClient

class MultiGoalActionClient:
    def __init__(self, action_server_name, action_type):
        self.action_server_name = action_server_name
        self.action_type = action_type
        # Create a single action client
        self.main_client = SimpleActionClient(self.action_server_name, self.action_type)
        self.action_client = self.main_client.action_client
        self.action_clients = []

    def wait_for_server(self):
        # Create a single action client and wait for the server
        self.main_client.wait_for_server()

    def send_goal(self, goal, done_cb=None, active_cb=None, feedback_cb=None):
        # Create a new action client using self.action_server_name and self.action_type
        action_client = SimpleActionClient(self.action_server_name, self.action_type)

        # Store the new action client instance
        self.action_clients.append(action_client)

        # Send the goal
        action_client.send_goal(goal, done_cb=done_cb, active_cb=active_cb, feedback_cb=feedback_cb)

    def cancel_all_goals(self):
        for client in self.action_clients:
            client.cancel_all_goals()

    def wait_for_result(self):
        return self.action_clients[-1].wait_for_result()
    
    def get_result(self):
        result = self.action_clients[-1].get_result()
        self.remove_action_client(self.action_clients[-1])
        return result
    
    def get_goal_state(self):
        return self.action_clients[-1].get_state()
    
    def get_goal_status_text(self):
        return self.action_clients[-1].get_goal_status_text()
    
    def get_goal_id(self):
        return self.action_clients[-1].get_goal_id()
    
    def get_num_goals(self):
        return len(self.action_clients)
    
    def remove_action_client(self, client):
        self.action_clients.remove(client)

    def remove_all_done_action_clients(self):
        for client in self.action_clients:
            # 3: DONE, 4: ACTIVE, 5: WAITING_FOR_RESULT, 8: RECALLED
            if client.get_state() in [3, 4, 5, 8]:
                self.remove_action_client(client)

    def __getattr__(self, name):
        return getattr(self.main_client, name)
